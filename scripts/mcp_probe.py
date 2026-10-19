"""Actual official-SDK stdio roundtrip on the local Chinese financial corpus."""
import asyncio
import json
from finagent.mcp_client import connect_mcp

async def main():
    async with connect_mcp() as bridge:
        result=await bridge.session.list_tools()
        names=[tool.name for tool in result.tools]
        assert {"search_reports","read_report_page","get_financial_metric","calculate_metric"} <= set(names)
        found=await bridge.call("search_reports",{"query":"营业收入 归母净利润","company":"德赛电池","period":"2024","top_k":3},"2025-08-31")
        assert found["results"], "No evidence returned"
        page=await bridge.call("read_report_page",{"document_id":"000049_2024","page":7},"2025-08-31")
        assert "20,859,251,590.68" in page["text"]
        calculation=await bridge.call("calculate_metric",
             {"operation":"growth_pct","values":["20859251590.68","20284918957.13"],"unit":"元","source_ids":["000049_2024:p7"]},"2025-08-31")
        assert calculation["value"]=="2.8313"
        rejected=False
        try:
            await bridge.call("search_reports",{"query":"收入"},"2025-09-01")
        except ValueError:
            rejected=True
        assert rejected
        print(json.dumps({"protocol":"2025-06-18","tools":names,"evidence_hits":len(found["results"]),
                          "growth_pct":calculation["value"],"future_rejected":rejected},ensure_ascii=False))

if __name__=="__main__":
    asyncio.run(main())
