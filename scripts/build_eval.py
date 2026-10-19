"""Build Chinese tasks from pre-cutoff reports; distinguish reviewed references from drafts."""
from decimal import Decimal
import json
from pathlib import Path
import re

from finagent.storage import connect

ROOT=Path(__file__).resolve().parents[1]
REVENUE=[
 ("000049","德赛电池",7,"20859251590.68"),
 ("000063","中兴通讯",11,"121298800000"),
 ("000651","格力电器",7,"189163654064.64"),
 ("000725","京东方A",11,"198380605661.00"),
 ("002008","大族激光",7,"14771215354.04"),
 ("002415","海康威视",9,"92495525118.30"),
 ("002475","立讯精密",8,"268794737612.58"),
 ("002594","比亚迪",10,"777102455000.00"),
 ("300124","汇川技术",9,"37040952138.72"),
 ("300207","欣旺达",8,"56020634117.81"),
]

def write_jsonl(path,rows):
    path.write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows)+"\n","utf-8")

def main():
    (ROOT/"eval").mkdir(exist_ok=True)
    verified=[]
    for code,name,page,value in REVENUE:
        verified.append({"id":code+"_revenue_2024","company":name,"code":code,
           "question":f"根据{name}2024年年报，2024年度营业收入是多少？给出金额、单位和原始页码。",
           "expected_value":value,"unit":"元","expected_pages":[f"{code}_2024:p{page}"],
           "as_of":"2025-08-31","annotation_status":"reviewed_original_excerpt",
           "split":"test" if code in ("002475","002594","300124","300207") else "dev"})
    extra=[
      ("000049","德赛电池",7,"归属于上市公司股东的净利润","413011992.42","元"),
      ("000049","德赛电池",7,"经营活动产生的现金流量净额","365595069.97","元"),
      ("000049","德赛电池",7,"总资产","16993299577.74","元"),
      ("000049","德赛电池",7,"归属于上市公司股东的净资产","6578542940.36","元"),
      ("000049","德赛电池",7,"基本每股收益","1.0738","元/股"),
      ("002008","大族激光",7,"归属于上市公司股东的净利润","1693900253.05","元"),
      ("002415","海康威视",9,"归属于上市公司股东的净利润","11977327023.54","元"),
      ("002475","立讯精密",8,"归属于上市公司股东的净利润","13365651026.16","元"),
    ]
    for index,(code,name,page,label,value,unit) in enumerate(extra):
        verified.append({"id":f"fact_{index}","company":name,"code":code,
          "question":f"根据{name}2024年年报，2024年度{label}是多少？给出数值、单位和原始页码。",
          "expected_value":value,"unit":unit,"expected_pages":[f"{code}_2024:p{page}"],
          "as_of":"2025-08-31","annotation_status":"reviewed_original_excerpt",
          "split":"test" if code=="002475" else "dev"})
    for label,a,b in (("营业收入","20859251590.68","20284918957.13"),
                      ("归母净利润","413011992.42","561870597.30")):
        value=(Decimal(a)-Decimal(b))/Decimal(b)*100
        verified.append({"id":"growth_"+str(len(verified)),"company":"德赛电池","code":"000049",
           "question":f"根据德赛电池2024年年报计算{label}同比变化百分比，说明计算式并引用原始页码。",
           "expected_value":str(value.quantize(Decimal("0.0001"))),"unit":"%",
           "expected_pages":["000049_2024:p7"],"as_of":"2025-08-31",
           "annotation_status":"reviewed_original_excerpt_and_decimal","split":"dev"})
    write_jsonl(ROOT/"eval/verified_facts.jsonl",verified)

    with connect() as conn:
        documents=[dict(x) for x in conn.execute("SELECT * FROM documents ORDER BY code,period")]
    companies={x["code"]:x["company"] for x in documents}
    tasks=[]
    for code,name in companies.items():
        split="test" if code in ("002475","002594","300124","300207") else "dev"
        queries=[]
        for year in ("2022","2023","2024"):
            for label in ("营业收入","归母净利润"):
                queries.append(("fact",f"根据{name}{year}年年报，{year}年度{label}是多少？核对单位并引用原始页码。",[f"{code}_{year}"]))
        queries.extend([
          ("comparison",f"比较{name}2023年和2024年的营业收入与归母净利润，给出可比口径、同比计算和页码。",[f"{code}_2023",f"{code}_2024"]),
          ("cashflow",f"分析{name}2024年经营现金流与归母净利润的差异，区分披露原因和分析推测。",[f"{code}_2024"]),
          ("period_alignment",f"能否把{name}2025年半年报收入直接与2024年全年收入计算同比？请解释并找出可比同期。",[f"{code}_2025H1",f"{code}_2024"]),
          ("unavailable",f"截至2025年8月31日，请给出{name}2025年年度报告披露的全年营业收入；资料不存在时请说明。",[])
        ])
        for kind,question,expected in queries:
            tasks.append({"id":f"{code}_{len(tasks):03d}","company":name,"code":code,"kind":kind,
                "question":question,"as_of":"2025-08-31","expected_documents":expected,
                "expected_behavior":"state_unavailable" if kind=="unavailable" else "answer_with_original_evidence",
                "annotation_status":"task_draft_requires_answer_review","split":split})
    write_jsonl(ROOT/"eval/tasks_100.jsonl",tasks)
    print(json.dumps({"reviewed_numeric_cases":len(verified),"task_drafts":len(tasks),
                      "note":"任务草稿不冒充已核验金标；检索指标不冒充模型答案准确率。"},ensure_ascii=False))

if __name__=="__main__":
    main()
