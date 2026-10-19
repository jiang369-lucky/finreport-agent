from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import validate_as_of
from .corpus import Corpus


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)
    company: str = Field(default="", max_length=50)
    period: str = Field(default="", max_length=10)
    top_k: int = Field(default=5, ge=1, le=10)


class PageInput(ToolInput):
    document_id: str = Field(max_length=80)
    page: int = Field(ge=1, le=2000)


class MetricInput(ToolInput):
    company: str = Field(min_length=1, max_length=50)
    period: str = Field(pattern=r"^20\d{2}$")
    metric: str = Field(min_length=1, max_length=80)


class CalculateInput(ToolInput):
    operation: Literal["growth_pct", "ratio_pct", "difference", "sum"]
    values: list[str] = Field(min_length=2, max_length=20)
    unit: Literal["元", "千元", "万元", "百万元", "亿元", "元/股", "无量纲"] = "元"
    source_ids: list[str] = Field(default_factory=list, max_length=20)


INPUTS = {"search_reports": SearchInput, "read_report_page": PageInput,
          "get_financial_metric": MetricInput, "calculate_metric": CalculateInput}
DESCRIPTIONS = {
    "search_reports": "检索中文财报，返回正文、报告期、披露日和页码；可用 company 过滤公司、period 过滤报告期。",
    "read_report_page": "读取财报完整原始页面，核对表格、单位和上下文。页码是 PDF 从 1 开始的物理页码。",
    "get_financial_metric": "查找主要财务指标的自动提取候选值，须回查页面核实；支持营业收入、归母净利润、经营现金流净额、总资产、归母净资产、基本每股收益、研发投入。",
    "calculate_metric": "使用 Decimal 精确计算；growth_pct: values=[本期,上期]，ratio_pct: [分子,分母]；输入必须先统一单位，source_ids 引用已读取的证据。",

}


def definitions():
    return [{"name": name, "description": DESCRIPTIONS[name], "inputSchema": model.model_json_schema()}
            for name, model in INPUTS.items()]


def calculate(operation, values, unit="元", source_ids=None):
    numbers = []
    for value in values:
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", value) or len(value) > 40:
            raise ValueError("仅接受已统一单位的有限十进制数字")
        numbers.append(Decimal(value))
    if operation in ("growth_pct", "ratio_pct", "difference") and len(numbers) != 2:
        raise ValueError("此计算必须提供两个数值")
    a, b = numbers[:2]
    note = ""
    if operation in ("growth_pct", "ratio_pct") and b == 0:
        raise ValueError("分母为零，不能计算")
    if operation == "growth_pct":
        result = (a - b) / abs(b) * 100
        formula = "(本期-上期)/abs(上期)*100"
        note = "上期为负时按绝对值作分母；此时应说明扭亏/减亏，不能与常规正基数增速混淆。" if b < 0 else ""
        output_unit = "%"
    elif operation == "ratio_pct":
        result, formula, output_unit = a / b * 100, "分子/分母*100", "%"
    elif operation == "difference":
        result, formula, output_unit = a - b, "本期-上期", unit
    elif operation == "sum":
        result, formula, output_unit = sum(numbers), "求和", unit
    else:
        raise ValueError("不支持的计算")
    return {"value": format(result.quantize(Decimal("0.0001")), "f"), "unit": output_unit,
            "inputs": values, "input_unit": unit, "formula": formula, "note": note,
            "source_ids": source_ids or []}


class ToolService:
    def __init__(self, corpus: Corpus):
        self.corpus = corpus

    def call(self, name, arguments, as_of):
        as_of = validate_as_of(as_of)
        if name not in INPUTS:
            raise ValueError("未提供该财报工具")
        args = INPUTS[name].model_validate(arguments).model_dump()
        if name == "search_reports":
            return {"results": self.corpus.search(as_of=as_of, **args)}
        if name == "read_report_page":
            return self.corpus.read_page(as_of=as_of, **args)
        if name == "get_financial_metric":
            return self.corpus.metric(as_of=as_of, **args)
        return calculate(**args)
