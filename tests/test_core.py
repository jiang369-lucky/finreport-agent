"""Deterministic reliability checks; does not call a paid model or claim LLM quality."""
import asyncio
from datetime import date
import json
import os
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "work" / "test-data"
assert DATA.resolve().is_relative_to((ROOT / "work").resolve())
os.environ["FIN_DATA_DIR"] = str(DATA)
# Never delete production rows: all regression fixtures live in a separate schema.
os.environ["FIN_DATABASE_SCHEMA"] = "finagent_test"

from finagent.config import settings, validate_as_of
from finagent.corpus import extract_metrics, Corpus
from finagent.harness import Harness
from finagent.memory import Memory, fit_context, estimate_tokens, SUMMARY_CHARS
from finagent.storage import connect, init_db, now
from finagent.tools import calculate, SearchInput, definitions

SOURCE = {"id": "000049_2024:p7", "document_id": "000049_2024", "page": 7,
          "company": "德赛电池", "title": "2024年年度报告", "period": "2024",
          "published": "2025-04-22", "url": "https://static.cninfo.com.cn/example.pdf",
          "text": "2024年营业收入100元，2023年营业收入80元。"}

def reset():
    init_db()
    with connect() as conn:
        for table in ("summaries","artifacts","runs","turns","sessions","metrics","chunks","pages","documents"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("INSERT INTO documents VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                     ("000049_2024","德赛电池","000049","2024年年度报告","2024","2025-04-22",
                      SOURCE["url"],"a"*64,"data/raw/not-used.pdf",7,now()))
        conn.execute("INSERT INTO pages VALUES(%s,%s,%s)",("000049_2024",7,SOURCE["text"]))

def call(name,args,cid="t1"):
    return {"role":"assistant","content":None,
            "tool_calls":[{"id":cid,"type":"function","function":{"name":name,"arguments":json.dumps(args,ensure_ascii=False)}}]}

def answer(text):
    return {"role":"assistant","content":text}

class ScriptedModel:
    def __init__(self,messages):
        self.messages=iter(messages)
        self.calls=0
        self.inputs=[]
    async def complete(self,messages,tools=None):
        self.calls+=1
        self.inputs.append([dict(x) for x in messages])
        return {"message":next(self.messages),"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}

class LoopModel:
    async def complete(self,messages,tools=None):
        return {"message":call("read_report_page",{"document_id":"000049_2024","page":7},f"t{len(messages)}"),
                "usage":{"total_tokens":15}}

class Bridge:
    def __init__(self):
        self.calls=[]
    async def call(self,name,args,as_of):
        self.calls.append((name,args,as_of))
        if name=="read_report_page":
            return dict(SOURCE)
        if name=="get_financial_metric":
            return {"candidates":[{**dict(SOURCE),"value":"100","unit":"元"}]}
        if name=="calculate_metric":
            return calculate(**args)
        raise ValueError("unsupported in fixture")

class FixtureCorpus:
    documents=[{"id":"000049_2024","company":"德赛电池","code":"000049"}]
    def search(self,*args,**kwargs):
        return [dict(SOURCE)]

class CoreTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.memory=Memory()

    def test_historical_boundary(self):
        self.assertEqual(validate_as_of("2025-08-31"),"2025-08-31")
        for value in ("2025-09-01","2026-01-01","garbage"):
            with self.assertRaises(ValueError):
                validate_as_of(value)

    def test_tool_schema_rejects_cutoff_injection(self):
        with self.assertRaises(ValueError):
            SearchInput.model_validate({"query":"利润","as_of":"2030-01-01"})

    def test_decimal_growth(self):
        result=calculate("growth_pct",["20859251590.68","20284918957.13"],"元",["000049_2024:p7"])
        self.assertEqual(result["value"],"2.8313")

    def test_calculation_zero_and_code_rejected(self):
        for values in (["1","0"],["__import__('os')","1"],["NaN","1"],["1,000","1"]):
            with self.assertRaises(ValueError):
                calculate("ratio_pct",values)

    def test_negative_base_explained(self):
        result=calculate("growth_pct",["2","-1"])
        self.assertEqual(result["value"],"300.0000")
        self.assertTrue(result["note"])

    def test_metric_columns_never_concatenated(self):
        rows=extract_metrics(["2024年 | 2023年 | 本年比上年增减",
                              "营业收入（元） | 20,859,251,590.68 | 20,284,918,957.13 | 2.83%"],"2024",7)
        self.assertEqual(rows[0]["value"],"20859251590.68")
        self.assertEqual(rows[1]["value"],"20284918957.13")

    def test_quarter_values_not_annual(self):
        rows=extract_metrics(["2024年 2023年 分季度主要财务指标",
                              "营业收入（元） | 100.00 | 80.00"],"2024",9)
        self.assertEqual(rows,[])

    def test_restated_previous_column_not_guessed(self):
        rows=extract_metrics(["2024年 | 2023年 | 调整前 | 调整后",
                              "营业收入（元） | 100.00 | 80.00 | 81.00 | 23.45%"],"2024",7)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["period"],"2024")

    def test_artifact_scope_and_pagination(self):
        first=self.memory.create_session()
        second=self.memory.create_session()
        aid=self.memory.save_artifact(first,"run1","read_report_page",{"large":"甲"*9000},"2025-08-31")
        part=self.memory.artifact(aid,first,"2025-08-31",0,100)
        self.assertEqual(part["next_offset"],100)
        with self.assertRaises(ValueError):
            self.memory.artifact(aid,second,"2025-08-31")
        with self.assertRaises(ValueError):
            self.memory.artifact(aid,first,"2024-01-01")

    def add_turn(self, sid, question, answer, as_of="2025-08-31", status="complete"):
        tid=self.memory.begin_turn(sid,question,as_of)
        self.memory.finish_turn(tid,answer,{"companies":["德赛电池"],"artifact_ids":["audit-only"]},status)
        return tid

    def test_empty_context(self):
        sid=self.memory.create_session()
        messages,info=self.memory.context(sid,"2025-08-31")
        self.assertEqual(messages,[])
        self.assertEqual(info["summary"],"")
        self.assertEqual(info["recent_turns"],0)
        self.assertEqual(info["summarized_turns"],0)

    def test_recent_turns_are_complete(self):
        sid=self.memory.create_session()
        expected=[]
        for index in range(4):
            question,reply=f"研究问题{index}",f"完整回答{index}"
            self.add_turn(sid,question,reply)
            expected.extend([{"role":"user","content":question},{"role":"assistant","content":reply}])
        messages,info=self.memory.context(sid,"2025-08-31")
        self.assertEqual(messages,expected)
        self.assertEqual(info["recent_turns"],4)
        self.assertEqual(info["summary"],"")

    def test_earlier_summary_and_recent_four(self):
        sid=self.memory.create_session()
        for index in range(9):
            self.add_turn(sid,f"德赛电池第{index}个研究问题",f"原始回答{index}")
        messages,info=self.memory.context(sid,"2025-08-31")
        self.assertEqual(info["summarized_turns"],5)
        self.assertEqual(info["recent_turns"],4)
        self.assertIn("德赛电池第0个研究问题",info["summary"])
        self.assertIn("原始回答4",info["summary"])
        self.assertNotIn("audit-only",info["summary"])
        self.assertNotIn("long_term",info)
        self.assertEqual(messages[1],{"role":"user","content":"德赛电池第5个研究问题"})
        self.assertEqual(messages[-1],{"role":"assistant","content":"原始回答8"})
        self.assertEqual(len(Memory().history(sid)),9)
        self.assertEqual(info["active_companies"],["德赛电池"])

    def test_summary_persists_and_window_does_not_drift(self):
        sid=self.memory.create_session()
        for index in range(8):
            self.add_turn(sid,f"研究问题{index}",f"研究答复{index}")
        messages,info=self.memory.context(sid,"2025-08-31")
        with connect() as conn:
            saved=conn.execute("SELECT content FROM summaries WHERE session_id=%s",(sid,)).fetchone()
        self.assertEqual(json.loads(saved["content"]),info["summary"])
        again,restored=Memory().context(sid,"2025-08-31")
        self.assertEqual(again,messages)
        self.assertEqual(restored,info)

    def test_summary_is_bounded_and_keeps_initial_topic(self):
        sid=self.memory.create_session()
        for index in range(30):
            self.add_turn(sid,f"初始公司研究问题{index}"+("问"*200),f"答复{index}"+("答"*2000))
        messages,info=self.memory.context(sid,"2025-08-31")
        self.assertLessEqual(len(info["summary"]),SUMMARY_CHARS)
        self.assertIn("初始公司研究问题0",info["summary"])
        self.assertEqual(info["recent_turns"],1)
        self.assertEqual(messages[-1]["content"],"答复29"+("答"*2000))
        self.assertEqual(len(self.memory.history(sid)),30)

    def test_large_history_turn_moves_to_summary_without_partial_dialogue(self):
        sid=self.memory.create_session()
        self.add_turn(sid,"较长的旧问题","旧回答"*3000)
        self.add_turn(sid,"近期问题","近期完整回答")
        messages,info=self.memory.context(sid,"2025-08-31")
        self.assertEqual(info["recent_turns"],1)
        self.assertEqual(info["summarized_turns"],1)
        self.assertEqual(messages[-2:],[
            {"role":"user","content":"近期问题"},{"role":"assistant","content":"近期完整回答"}])

    def test_latest_full_turn_is_retained_even_above_history_target(self):
        sid=self.memory.create_session()
        self.add_turn(sid,"上一轮问题","上一轮完整回答"*1000)
        messages,info=self.memory.context(sid,"2025-08-31")
        self.assertEqual(info["recent_turns"],1)
        self.assertEqual(info["summarized_turns"],0)
        self.assertEqual(messages[-1]["content"],"上一轮完整回答"*1000)
        self.assertGreater(estimate_tokens(messages),2300)
        with self.assertRaises(ValueError):
            fit_context(messages,[],100)

    def test_context_session_date_and_failed_turn_isolation(self):
        sid=self.memory.create_session()
        other=self.memory.create_session()
        self.add_turn(sid,"允许的历史问题","允许的答复","2025-07-01")
        self.add_turn(sid,"较晚的资料","不得泄漏","2025-08-31")
        self.add_turn(sid,"失败的问题","失败的答复","2025-07-01",status="failed")
        self.add_turn(other,"其他会话问题","其他会话答复","2025-07-01")
        messages,info=self.memory.context(sid,"2025-07-01")
        self.assertEqual(messages,[{"role":"user","content":"允许的历史问题"},
                                   {"role":"assistant","content":"允许的答复"}])
        self.assertEqual(info["recent_turns"],1)

    def test_old_structured_summary_is_rebuilt_from_original_turns(self):
        sid=self.memory.create_session()
        for index in range(5):
            tid=self.add_turn(sid,f"真实问题{index}",f"真实答复{index}")
        with connect() as conn:
            conn.execute("INSERT INTO summaries VALUES(%s,%s,%s,%s,%s)",
                (sid,"2025-08-31",tid,json.dumps({"tasks":["legacy"],"artifact_ids":["legacy-id"]}),now()))
        messages,info=self.memory.context(sid,"2025-08-31")
        self.assertIn("真实问题0",info["summary"])
        self.assertNotIn("legacy",str(messages))
        self.assertEqual(info["recent_turns"],4)

    def test_model_has_only_four_financial_tools(self):
        self.assertEqual({x["name"] for x in definitions()},
            {"search_reports","read_report_page","get_financial_metric","calculate_metric"})

    def test_current_tool_messages_are_not_rewritten(self):
        messages=[{"role":"system","content":"规则"},{"role":"user","content":"问题"},
            call("read_report_page",{"document_id":"000049_2024","page":7}),
            {"role":"tool","tool_call_id":"t1","content":json.dumps(SOURCE,ensure_ascii=False)}]
        kept,info=fit_context(messages,[],4000)
        self.assertEqual(kept,messages)
        self.assertNotIn("compacted_artifacts",info)

    def test_context_rejects_unshrinkable_input(self):
        with self.assertRaises(ValueError):
            fit_context([{"role":"system","content":"规"*1000},{"role":"user","content":"问"*1000}],[],100)

    def test_page_cutoff_filters_at_database(self):
        corpus=Corpus()
        with self.assertRaises(ValueError):
            corpus.read_page("000049_2024",7,"2025-01-01")


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset()
        self.memory=Memory()
        self.sid=self.memory.create_session()
        self.bridge=Bridge()

    async def test_real_graph_tools_and_citations(self):
        model=ScriptedModel([call("read_report_page",{"document_id":"000049_2024","page":7}),
                             call("calculate_metric",{"operation":"growth_pct","values":["100","80"],
                                                      "unit":"元","source_ids":["000049_2024:p7"]},"t2"),
                             answer("收入增长25%。[000049_2024:p7]")])
        result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"德赛电池营收同比")
        self.assertEqual(result["status"],"complete")
        self.assertEqual(result["tool_calls"],2)
        self.assertEqual(len(result["artifact_ids"]),2)
        self.assertEqual(result["usage"]["total_tokens"],45)
        self.assertTrue(all(x[2]=="2025-08-31" for x in self.bridge.calls))

    async def test_unknown_tool_does_not_execute(self):
        model=ScriptedModel([call("delete_files",{}),answer("无法执行该操作。")])
        result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"说明你支持的功能")
        self.assertFalse(self.bridge.calls)
        self.assertTrue(any(x.get("ok") is False for x in result["events"]))

    async def test_fabricated_citation_repaired(self):
        model=ScriptedModel([call("read_report_page",{"document_id":"000049_2024","page":7}),
            answer("收入100元。[000049_2024:p999]"),answer("收入100元。[000049_2024:p7]")])
        result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"德赛电池收入")
        self.assertEqual(result["status"],"complete")
        self.assertIn("citation_repair",[x["kind"] for x in result["events"]])
        self.assertNotIn("p999",result["answer"])

    async def test_budget_stops_loop(self):
        result=await Harness(FixtureCorpus(),self.bridge,LoopModel()).run(self.sid,"德赛电池收入")
        self.assertEqual(result["status"],"budget_exhausted")
        self.assertLessEqual(result["tool_calls"],settings.max_tool_calls)

    async def test_retired_memory_tools_are_rejected(self):
        for name in ("read_artifact","read_memory","save_research_note"):
            with self.subTest(tool=name):
                model=ScriptedModel([call(name,{}),answer("当前支持财报研究工具。")])
                result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"说明支持的功能")
                self.assertEqual(result["status"],"complete")
                self.assertTrue(any(x.get("name")==name and x.get("ok") is False for x in result["events"]))
        self.assertFalse(self.bridge.calls)

    async def test_harness_passes_summary_and_recent_turns_to_model(self):
        for index in range(7):
            tid=self.memory.begin_turn(self.sid,f"历史问题{index}","2025-08-31")
            self.memory.finish_turn(tid,f"历史回答{index}",{"companies":["德赛电池"]})
        model=ScriptedModel([answer("可以继续研究。")])
        result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"继续研究")
        self.assertEqual(result["status"],"complete")
        self.assertEqual(result["memory"]["recent_turns"],4)
        self.assertEqual(result["memory"]["summarized_turns"],3)
        sent=model.inputs[0]
        self.assertIn("历史问题0",sent[1]["content"])
        self.assertEqual(sent[2],{"role":"user","content":"历史问题3"})
        self.assertEqual(sent[-2],{"role":"assistant","content":"历史回答6"})
        self.assertEqual(sent[-1],{"role":"user","content":"继续研究"})

    async def test_retrieval_mode_has_no_model_calls(self):
        model=ScriptedModel([])
        result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"德赛电池收入",mode="retrieval")
        self.assertEqual(model.calls,0)
        self.assertIn("未调用大模型",result["answer"])
        self.assertEqual(result["mode"],"retrieval")


    async def test_candidate_only_citation_rejected(self):
        model=ScriptedModel([call("get_financial_metric",{"company":"德赛电池","period":"2024","metric":"营业收入"}),
            answer("收入100元。[000049_2024:p7]"),answer("收入100元。[000049_2024:p7]")])
        result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"德赛电池收入")
        self.assertEqual(result["status"],"needs_review")

    async def test_parallel_attempts_never_exceed_tool_budget(self):
        request=call("read_report_page",{"document_id":"000049_2024","page":7})
        request["tool_calls"]=[{**request["tool_calls"][0],"id":"p"+str(i)} for i in range(4)]
        model=ScriptedModel([request])
        with patch("finagent.harness.settings",replace(settings,max_tool_calls=2)):
            result=await Harness(FixtureCorpus(),self.bridge,model).run(self.sid,"德赛电池收入")
        self.assertEqual(len(self.bridge.calls),2)
        self.assertEqual(result["tool_calls"],2)
        self.assertEqual(result["status"],"budget_exhausted")

if __name__=="__main__":
    unittest.main(verbosity=2)
