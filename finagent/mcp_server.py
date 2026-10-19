"""Official MCP SDK 1.13.1 server; protocol generation 2025-06-18."""
from functools import lru_cache
import json

from mcp.server.fastmcp import FastMCP

from .config import settings, validate_as_of
from .corpus import Corpus
from .tools import ToolService

mcp = FastMCP("中文财报研究工具")


@lru_cache(maxsize=1)
def service():
    return ToolService(Corpus())


@mcp.tool()
def search_reports(query: str, company: str = "", period: str = "", top_k: int = 5,
                   as_of: str = "2025-08-31") -> dict:
    """检索截止日前的中文财报，返回证据正文、披露日与 PDF 物理页码。"""
    return service().call("search_reports", {"query": query, "company": company, "period": period, "top_k": top_k}, as_of)


@mcp.tool()
def read_report_page(document_id: str, page: int, as_of: str = "2025-08-31") -> dict:
    """读取原始财报一整页，核对指标、金额单位、表头和脚注。"""
    return service().call("read_report_page", {"document_id": document_id, "page": page}, as_of)


@mcp.tool()
def get_financial_metric(company: str, period: str, metric: str, as_of: str = "2025-08-31") -> dict:
    """获取自动提取的指标候选；须回查原始页面，禁止把候选值直接当作核验结论。"""
    return service().call("get_financial_metric", {"company": company, "period": period, "metric": metric}, as_of)


@mcp.tool()
def calculate_metric(operation: str, values: list[str], unit: str = "元", source_ids: list[str] | None = None,
                     as_of: str = "2025-08-31") -> dict:
    """Decimal 计算同比、占比、差额或求和；values 必须先统一单位。"""
    return service().call("calculate_metric", {"operation": operation, "values": values, "unit": unit,
                                              "source_ids": source_ids or []}, as_of)


@mcp.resource("finreport://catalog")
def catalog() -> str:
    docs = [x for x in service().corpus.documents if x["published"] <= settings.cutoff]
    return json.dumps(docs, ensure_ascii=False)


@mcp.prompt()
def financial_comparison(company: str, period: str = "2024") -> str:
    return f"分析{company}{period}年度收入、归母净利润与经营现金流；先读取原始证据和金额单位，再调用计算工具，引用PDF物理页码。"


if __name__ == "__main__":
    mcp.run(transport="stdio")
