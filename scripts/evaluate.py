"""Compare retrieval, one-shot RAG, and actual MCP Agent. Live modes require the user's key."""
import argparse
import asyncio
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import time

from finagent.config import settings
from finagent.corpus import Corpus
from finagent.harness import Harness
from finagent.llm import QwenClient
from finagent.mcp_client import connect_mcp
from finagent.memory import Memory, fit_context
from finagent.storage import now

ROOT=Path(__file__).resolve().parents[1]

def numeric_present(text,expected,unit):
    target=Decimal(expected)
    scales={"元":Decimal(1),"千元":Decimal(1000),"万元":Decimal(10000),
            "百万元":Decimal(1000000),"亿元":Decimal(100000000),"元/股":Decimal(1),"%":Decimal(1)}
    pattern=r"(-?\d[\d,]*(?:\.\d+)?)\s*(元/股|百万元|亿元|万元|千元|元|%)?"
    for match in re.finditer(pattern,text):
        try:
            value=Decimal(match.group(1).replace(",",""))
            reported_unit=match.group(2) or unit
            if unit=="%" and reported_unit!="%":
                continue
            if unit=="元/股" and reported_unit!="元/股":
                continue
            value*=scales.get(reported_unit,Decimal(1))
            if abs(value-target)<=max(abs(target)*Decimal("0.00005"),Decimal("0.00005")):
                return True
        except InvalidOperation:
            continue
    return False

async def run(args):
    rows=[json.loads(x) for x in (ROOT/args.dataset).read_text("utf-8").splitlines() if x]
    if args.split:
        rows=[x for x in rows if x["split"]==args.split]
    rows=rows[:args.limit]
    if args.mode!="retrieval" and not settings.configured:
        raise SystemExit("真实模型评测需要先在 .env 中配置通义千问密钥。")
    corpus=Corpus()
    outputs=[]
    model=QwenClient()
    async with connect_mcp() as bridge:
        harness=Harness(corpus,bridge,model)
        for case in rows:
            started=time.perf_counter()
            years=set(re.findall(r"20\d{2}",case["question"]))
            period=next(iter(years)) if len(years)==1 else ""
            hits=corpus.search(case["question"],case["company"],case["as_of"],5,period)
            pages={f"{x['document_id']}:p{x['page']}" for x in hits}
            output={"case_id":case["id"],"question":case["question"],"mode":args.mode,
                "annotation_status":case["annotation_status"],"split":case["split"],
                "retrieved_pages":sorted(pages),"evidence_page_hit_at_5":
                    bool(pages.intersection(case.get("expected_pages",[]))) if "expected_pages" in case else None}
            if args.mode=="retrieval":
                output.update({"answer":None,"numeric_value_present":None,"status":"retrieved","usage":{}})
            elif args.mode=="rag":
                evidence=[{"citation_id":f"{x['document_id']}:p{x['page']}","text":x["text"],"published":x["published"]} for x in hits]
                messages=[{"role":"system","content":"仅依据提供的历史财报证据，用中文回答。金额核对单位。引用格式 [document_id:p页码]。资料不足时说明。"},
                          {"role":"user","content":case["question"]+"\n证据："+json.dumps(evidence,ensure_ascii=False)}]
                messages,_=fit_context(messages,[],settings.context_tokens-settings.completion_tokens-500)
                response=await model.complete(messages)
                output.update({"answer":response["message"].get("content",""),"usage":response["usage"],"status":"complete"})
            else:
                sid=Memory().create_session("评测 "+case["id"])
                result=await harness.run(sid,case["question"],case["as_of"])
                output.update({key:result[key] for key in ("answer","usage","status","run_id","tool_calls")})
            if output["answer"] is not None and "expected_value" in case:
                output["numeric_value_present"]=numeric_present(output["answer"],case["expected_value"],case["unit"])
                citations=set(re.findall(r"\[([A-Za-z0-9_]+:p\d+)\]",output["answer"]))
                output["expected_page_cited"]=bool(citations.intersection(case.get("expected_pages",[])))
            output["latency_ms"]=round((time.perf_counter()-started)*1000)
            output["human_answer_review"]="pending" if args.mode!="retrieval" else "not_applicable"
            outputs.append(output)
            print(case["id"],output["status"],flush=True)
    graded=[x for x in outputs if x["evidence_page_hit_at_5"] is not None]
    summary={"mode":args.mode,"dataset":args.dataset,"cases":len(outputs),"model":settings.model,
             "cutoff":settings.cutoff,"evaluated_at":now(),
             "evidence_page_recall_at_5":sum(x["evidence_page_hit_at_5"] for x in graded)/len(graded) if graded else None,
             "live_model_called":args.mode!="retrieval",
             "note":"检索页命中率不是答案准确率；数值出现与页码引用只作诊断，真实答案正确性仍需人工审核。",
             "runs":outputs}
    path=ROOT/"eval"/f"results_{args.mode}.json"
    path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),"utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k!="runs"},ensure_ascii=False))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--mode",choices=["retrieval","rag","agent"],default="retrieval")
    parser.add_argument("--dataset",default="eval/verified_facts.jsonl")
    parser.add_argument("--limit",type=int,default=20)
    parser.add_argument("--split",choices=["dev","test"])
    args=parser.parse_args()
    if args.limit<1 or args.limit>100:
        raise SystemExit("limit 须在 1–100 之间")
    asyncio.run(run(args))

if __name__=="__main__":
    main()
