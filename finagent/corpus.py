from collections import defaultdict
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import re

import jieba
import pymupdf
from rank_bm25 import BM25Okapi

from .chunking import chunk_page_sources, document_chunks
from .config import ROOT, settings, validate_as_of
from .storage import connect, dump, init_db, now

jieba.setLogLevel(logging.ERROR)
STOP = {"的", "了", "是", "在", "与", "和", "及", "请", "根据", "公司", "报告", "年", "情况", "分析"}
ALIASES = {
    "营业收入": ("营业收入", "营业总收入"),
    "归母净利润": ("归属于上市公司股东的净利润", "归属于上市公司普通股股东的净利润", "归属于母公司股东的净利润"),
    "经营现金流净额": ("经营活动产生的现金流量净额",),
    "总资产": ("总资产", "资产总额"),
    "归母净资产": ("归属于上市公司股东的净资产", "归属于母公司股东的净资产"),
    "基本每股收益": ("基本每股收益",),
    "研发投入": ("研发投入金额", "研发费用"),
}
NUMBER = re.compile(r"(?<![A-Za-z\d.,])(?:-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?:%)?(?![\d.,])")
YEAR = re.compile(r"20\d{2}")


@lru_cache(maxsize=30000)
def tokenize(text: str) -> tuple[str, ...]:
    terms = jieba.lcut(text.lower())
    return tuple(x for x in terms if re.search(r"[\w\u4e00-\u9fff]", x) and x not in STOP)


def expand_query(query: str) -> str:
    expansions = []
    for phrase, target in (("赚了", "归母净利润"), ("赚多少", "归母净利润"), ("现金流", "经营活动产生的现金流量净额"),
                            ("营收", "营业收入"), ("归母", "归属于上市公司股东的净利润"),
                            ("研发", "研发投入"), ("负债", "资产负债表")):
        if phrase in query:
            expansions.append(target)
    return query + " " + " ".join(expansions)


def retrieval_query(query: str, companies=()) -> str:
    text = query
    for company in companies:
        text = text.replace(company, " ")
    text = re.sub(r"20\d{2}年?", " ", text)
    boilerplate = r"根据|依据|请问|请|年度报告|年报|年度|给出|是多少|多少|原始|页码|金额|单位|数值|核对|引用|并|说明|计算式|百分比"
    text = re.sub(boilerplate, " ", text)
    return expand_query(text.strip() or query)


def page_lines(page) -> list[str]:
    words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    rows = []
    baseline = None
    for word in words:
        if baseline is None or abs(word[1] - baseline) > 3:
            rows.append([])
            baseline = word[1]
        rows[-1].append(word)
    return [" ".join(w[4] for w in sorted(row, key=lambda w: w[0])) for row in rows]


def table_lines(page) -> list[str]:
    """Preserve cell boundaries; join wrapped digits only within a detected cell."""
    import contextlib
    import sys
    with contextlib.redirect_stdout(sys.stderr):
        tables = page.find_tables().tables
    return [" | ".join(re.sub(r"\s+", "", cell or "") for cell in row)
            for table in tables for row in table.extract()]


def metric_lines(page) -> list[str]:
    raw = page_lines(page)
    tables = table_lines(page)
    units = [line for line in raw if re.search(r"单位\s*[:：]", line)]
    return units + tables if tables else raw


def extract_metrics(lines: list[str], period: str, page_number: int) -> list[dict]:
    if page_number > 18 or not period.isdigit():
        return []
    joined = "\n".join(lines)
    compact_page = re.sub(r"\s+", "", joined)
    if not any(x in compact_page for x in ("主要会计数据", "主要财务指标", "营业收入", "营业总收入")):
        return []
    if any(x in compact_page for x in ("分季度主要财务指标", "第一季度", "第二季度")):
        return []
    year = int(period)
    rows = []
    for index, line in enumerate(lines):
        compact = re.sub(r"\s+", "", line)
        for label, aliases in ALIASES.items():
            matched = next((alias for alias in aliases if alias in compact), None)
            if not matched or len(line) > 600 or any(x in compact for x in ("增长率", "占比", "比重")):
                continue
            label_match = re.search(r"\s*".join(re.escape(c) for c in matched), line)
            if not label_match:
                continue
            suffix = line[label_match.end():]
            numbers = [m.group() for m in NUMBER.finditer(suffix) if not m.group().endswith("%")]
            if len(numbers) < 2:
                continue
            header = " ".join(lines[max(0, index - 18):index])
            if str(year) not in YEAR.findall(header) or str(year - 1) not in YEAR.findall(header):
                continue
            unit = "元/股" if label == "基本每股收益" else ""
            unit_match = re.search(r"单位[:：]?\s*[（(]?\s*(百万元|亿元|万元|千元|元)", joined[:6000])
            if unit_match:
                unit = unit_match.group(1)
            own_unit = re.search(re.escape(matched) + r"[（(](元/股|百万元|亿元|万元|千元|元)", compact)
            if own_unit:
                unit = own_unit.group(1)
            if not unit:
                continue
            selected = numbers[:1] if "调整前" in header and "调整后" in header else numbers[:2]
            for offset, raw in enumerate(selected):
                try:
                    value = str(Decimal(raw.replace(",", "")))
                except InvalidOperation:
                    continue
                if value in (str(year), str(year - 1)):
                    continue
                rows.append({"label": label, "period": str(year-offset), "value": value, "unit": unit,
                             "excerpt": "\n".join(lines[max(0,index-3):index+2]), "confidence": "candidate"})
    return rows


def ingest_manifest(manifest_path: Path | None = None) -> dict:
    init_db()
    manifest_path = manifest_path or settings.data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    count = reindexed = pages = chunks = metrics = 0
    errors = []
    for record in manifest["documents"]:
        try:
            validate_as_of(record["published"])
            path = (ROOT / record["path"]).resolve()
            raw_root = (settings.data_dir / "raw").resolve()
            if not path.is_relative_to(raw_root):
                raise ValueError("PDF 路径不在 data/raw 内")
            if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                raise ValueError("原始 PDF 的 SHA256 与清单不一致")
            with connect() as conn:
                old = conn.execute("SELECT sha256 FROM documents WHERE id=%s", (record["id"],)).fetchone()
                unchanged = old and old["sha256"] == record["sha256"]
                if unchanged and conn.execute(
                    "SELECT 1 FROM chunk_metadata m JOIN chunks c ON c.id=m.chunk_id "
                    "WHERE c.document_id=%s LIMIT 1", (record["id"],)).fetchone():
                    continue
            with pymupdf.open(path) as pdf, connect() as conn:
                doc_chunks = doc_metrics = 0
                document_pages = len(pdf)
                if unchanged:
                    conn.execute("DELETE FROM chunks WHERE document_id=%s", (record["id"],))
                else:
                    conn.execute("DELETE FROM documents WHERE id=%s", (record["id"],))
                    conn.execute("INSERT INTO documents VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (record["id"], record["company"], record["code"], record["title"], record["period"],
                         record["published"], record["url"], record["sha256"], record["path"], len(pdf), now()))
                    for page_index, page in enumerate(pdf):
                        page_no = page_index + 1
                        text = page.get_text("text", sort=True).replace("\x00", "")
                        conn.execute("INSERT INTO pages VALUES(%s,%s,%s)", (record["id"], page_no, text))
                        if page_no <= 18:
                            for ordinal, metric in enumerate(extract_metrics(metric_lines(page), record["period"], page_no)):
                                mid = f"{record['id']}:p{page_no}:m{ordinal}"
                                conn.execute("INSERT INTO metrics VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                                    (mid, record["id"], page_no, metric["label"], metric["period"], metric["value"],
                                     metric["unit"], metric["excerpt"], metric["confidence"]))
                                doc_metrics += 1
                for ordinal, chunk in enumerate(document_chunks(pdf)):
                    cid = f"{record['id']}:p{chunk['page']}:c{ordinal}"
                    indexed_text = " ".join(chunk["section_path"]) + "\n" + chunk["text"]
                    conn.execute("INSERT INTO chunks VALUES(%s,%s,%s,%s,%s,%s)",
                        (cid, record["id"], chunk["page"], ordinal, chunk["text"], " ".join(tokenize(indexed_text))))
                    conn.execute("INSERT INTO chunk_metadata VALUES(%s,%s,%s)",
                        (cid, dump(chunk["section_path"]), dump(chunk["sources"])))
                    doc_chunks += 1
            if unchanged:
                reindexed += 1
            else:
                count += 1
                pages += document_pages
            chunks += doc_chunks
            metrics += doc_metrics
            print(f"已索引 {record['id']}: {document_pages} / {doc_chunks} 片段 / {doc_metrics} 指标候选", flush=True)
        except Exception as exc:
            errors.append({"id": record["id"], "error": str(exc)})
    result = {"new_documents": count, "reindexed_documents": reindexed, "new_pages": pages, "new_chunks": chunks,
              "new_metrics": metrics, "errors": errors}
    (settings.data_dir / "ingestion-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    return result


class Corpus:
    def __init__(self):
        init_db()
        with connect() as conn:
            self.rows = [dict(x) for x in conn.execute(
                "SELECT c.*,d.company,d.code,d.title,d.period,d.published,d.url,m.section_path,m.sources "
                "FROM chunks c JOIN documents d ON d.id=c.document_id "
                "LEFT JOIN chunk_metadata m ON m.chunk_id=c.id ORDER BY c.id")]
        for row in self.rows:
            row["section_path"] = json.loads(row["section_path"]) if row["section_path"] else []
            row["sources"] = json.loads(row["sources"]) if row["sources"] else []
            row["source_pages"] = list(dict.fromkeys(s["page"] for s in row["sources"])) or [row["page"]]
        self.bm25 = BM25Okapi([x["tokens"].split() or ["空"] for x in self.rows]) if self.rows else None
        with connect() as conn:
            self.documents = [dict(x) for x in conn.execute("SELECT * FROM documents ORDER BY code,period")]
        self.highlight_pages = set()
        with connect() as conn:
            first_pages = [dict(row) for row in conn.execute(
                "SELECT document_id,page,text FROM pages WHERE page<=22")]
        for row in first_pages:
            compact = re.sub(r"\s+", "", row["text"])
            if (any(term in compact for term in ("主要会计数据", "主要财务数据", "主要财务指标"))
                and ("营业收入" in compact or "营业总收入" in compact)
                and re.search(r"\d{1,3},\d{3}", row["text"])):
                self.highlight_pages.add((row["document_id"], row["page"]))
        original = set(self.highlight_pages)
        for row in first_pages:
            if (row["document_id"], row["page"]-1) in original and any(
                term in re.sub(r"\s+", "", row["text"]) for term in ("归属于", "现金流量净额", "总资产")):
                self.highlight_pages.add((row["document_id"], row["page"]))

    def search(self, query: str, company: str = "", as_of: str | None = None, top_k: int = 5, period: str = "") -> list[dict]:
        as_of = validate_as_of(as_of)
        if not self.bm25:
            return []
        if not period:
            years = set(re.findall(r"20\d{2}", query))
            if len(years) == 1:
                period = next(iter(years))
        cleaned = retrieval_query(query, [d["company"] for d in self.documents])
        query_terms = tokenize(cleaned)
        numeric_focus = any(term in query for term in ("多少", "数值", "同比", "营业收入", "归母净利润", "总资产", "每股收益"))
        numeric_focus = numeric_focus and not any(term in query for term in ("原因", "构成", "附注", "差异", "风险"))

        scores = self.bm25.get_scores(query_terms)
        ranked = []
        for index, row in enumerate(self.rows):
            if row["published"] > as_of:
                continue
            if company and company not in (row["company"], row["code"]) and company not in row["company"]:
                continue
            if period and row["period"] != period:
                continue
            if scores[index] <= 0:
                continue
            score = float(scores[index])
            if numeric_focus and any((row["document_id"], page) in self.highlight_pages for page in row["source_pages"]):
                score *= 2.2
            ranked.append((score, index))
        ranked.sort(reverse=True)
        results = []
        used = defaultdict(int)
        for score, index in ranked:
            row = self.rows[index]
            page_key = (row["document_id"], row["page"])
            if used[page_key] >= 2:
                continue
            used[page_key] += 1
            results.append({k: row[k] for k in ("id", "document_id", "company", "code", "title", "period", "published", "url", "page", "text")})
            results[-1].update({"section_path": row["section_path"], "source_pages": row["source_pages"],
                               "sources": chunk_page_sources(row)})
            results[-1]["score"] = round(score, 4)
            if len(results) >= max(1, min(top_k, 12)):
                break
        return results

    def read_page(self, document_id: str, page: int, as_of: str | None = None) -> dict:
        as_of = validate_as_of(as_of)
        with connect() as conn:
            row = conn.execute(
                "SELECT p.*,d.company,d.code,d.title,d.period,d.published,d.url FROM pages p "
                "JOIN documents d ON d.id=p.document_id WHERE p.document_id=%s AND p.page=%s AND d.published<=%s",
                (document_id, page, as_of)).fetchone()
        if not row:
            raise ValueError("未找到截止日内的该页")
        result = {**dict(row), "id": f"{document_id}:p{page}"}
        doc = next((item for item in self.documents if item["id"] == document_id), None)
        if doc:
            with pymupdf.open(ROOT / doc["path"]) as pdf:
                try:
                    result["table_rows"] = table_lines(pdf[page-1])[:100]
                except (ValueError, RuntimeError):
                    result["table_rows"] = []
        return result

    def metric(self, company: str, period: str, metric: str, as_of: str | None = None) -> dict:
        as_of = validate_as_of(as_of)
        canonical = next((key for key, aliases in ALIASES.items() if metric == key or metric in aliases), metric)
        with connect() as conn:
            rows = conn.execute(
                "SELECT m.*,d.company,d.code,d.title,d.period AS report_period,d.published,d.url "
                "FROM metrics m JOIN documents d ON d.id=m.document_id "
                "WHERE (d.company LIKE %s OR d.code=%s) AND m.period=%s AND m.label=%s AND d.published<=%s "
                "ORDER BY (d.period=m.period) DESC,d.published ASC",
                ("%" + company + "%", company, period, canonical, as_of)).fetchall()
        unique = []
        seen = set()
        for row in rows:
            item = dict(row)
            key = (item["document_id"], item["value"], item["unit"])
            if key not in seen:
                seen.add(key)
                unique.append(item)
        return {"metric": canonical, "period": period, "candidates": unique[:6],
                "warning": "自动提取的候选值，须核对原始页及单位；不同报告可能使用重述值。"
                           if unique else "未提取到可靠候选值，请检索并读取原始财报页面。",
                "fallback_query": f"{company} {period} {canonical}"}

    def stats(self) -> dict:
        with connect() as conn:
            counts = {table: conn.execute(f"SELECT COUNT(*) AS value FROM {table}").fetchone()["value"]
                      for table in ("documents", "pages", "chunks", "metrics")}
            counts["companies"] = conn.execute("SELECT COUNT(DISTINCT code) AS value FROM documents").fetchone()["value"]
            counts["latest_publication"] = conn.execute("SELECT MAX(published) AS value FROM documents").fetchone()["value"]
        return counts
