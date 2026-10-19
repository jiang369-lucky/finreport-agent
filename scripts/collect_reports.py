"""Collect original Chinese disclosures. No paid API or current-market data is used."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = "2025-08-31"
TZ = timezone(timedelta(hours=8))
COMPANIES = {
    "000049": "德赛电池", "000063": "中兴通讯", "002594": "比亚迪",
    "300207": "欣旺达", "002475": "立讯精密", "002008": "大族激光",
    "300124": "汇川技术", "002415": "海康威视", "000725": "京东方A", "000651": "格力电器",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (Financial report academic project)",
           "Referer": "https://www.cninfo.com.cn/", "Accept": "application/json, text/plain, */*"}


def request(url, form=None):
    data = urllib.parse.urlencode(form).encode() if form else None
    headers = dict(HEADERS)
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    error = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except Exception as exc:
            error = exc
            time.sleep(1 + attempt)
    raise RuntimeError(f"请求失败: {url}: {error}")


def period_from_title(title):
    clean = re.sub(r"\s+", "", title)
    if any(word in clean for word in ("摘要", "英文", "英文版", "取消", "审计报告")):
        return None
    for year in ("2022", "2023", "2024"):
        if re.search(year + r"年(?:年度报告|年报)", clean):
            return year
    if re.search(r"2025年(?:半年度报告|半年度财务报告)", clean):
        return "2025H1"
    return None


def discover():
    stocks = json.loads(request("https://www.cninfo.com.cn/new/data/szse_stock.json"))["stockList"]
    orgs = {x["code"]: x["orgId"] for x in stocks}
    selected = {}
    failures = []
    for code, name in COMPANIES.items():
        try:
            for page in range(1, 5):
                body = {
                    "pageNum": page, "pageSize": 30, "column": "szse", "tabName": "fulltext",
                    "stock": code + "," + orgs[code], "searchkey": "", "secid": "", "plate": "",
                    "category": "category_ndbg_szsh;category_bndbg_szsh;",
                    "trade": "", "seDate": "2023-01-01~2025-08-31",
                    "sortName": "time", "sortType": "desc", "isHLtitle": "true",
                }
                result = json.loads(request("https://www.cninfo.com.cn/new/hisAnnouncement/query", body))
                announcements = result.get("announcements") or []
                for item in announcements:
                    title = re.sub("<[^>]+>", "", item["announcementTitle"])
                    period = period_from_title(title)
                    if not period:
                        continue
                    published = datetime.fromtimestamp(item["announcementTime"] / 1000, TZ).date().isoformat()
                    if published > CUTOFF:
                        continue
                    # Prefer the original disclosure, preserving any earlier version instead of silently taking a later revision.
                    key = code + "_" + period
                    url = "https://static.cninfo.com.cn/" + item["adjunctUrl"].lstrip("/")
                    record = {"id": key, "company": name, "code": code, "title": title,
                              "period": period, "published": published, "url": url,
                              "announcement_id": str(item["announcementId"])}
                    if key not in selected or published < selected[key]["published"]:
                        selected[key] = record
                if not result.get("hasMore") or not announcements:
                    break
                time.sleep(0.4)
            print(f"发现 {code} {name}: {sum(x['code'] == code for x in selected.values())} 份", flush=True)
        except Exception as exc:
            failures.append({"code": code, "error": str(exc)})
        time.sleep(0.4)
    return sorted(selected.values(), key=lambda x: x["id"]), failures


def download(record):
    target = ROOT / "data" / "raw" / (record["id"] + ".pdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        content = request(record["url"])
        if not content.lstrip().startswith(b"%PDF") or len(content) > 70_000_000:
            raise ValueError("来源未返回有效 PDF，或文件超过 70 MB")
        temporary = target.with_suffix(".part")
        temporary.write_bytes(content)
        temporary.replace(target)
    content = target.read_bytes()
    if not content.lstrip().startswith(b"%PDF"):
        raise ValueError("已有文件不是 PDF")
    return {**record, "path": str(target.relative_to(ROOT)).replace("\\", "/"),
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    records, failures = discover()
    output = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(download, record): record for record in records[:args.limit]}
        for future in as_completed(futures):
            record = futures[future]
            try:
                output.append(future.result())
                print(f"已下载 {record['id']} {record['title']}", flush=True)
            except Exception as exc:
                failures.append({"id": record["id"], "error": str(exc)})
    manifest = {"cutoff": CUTOFF, "source": "巨潮资讯原始披露",
                "retrieved_at": datetime.now(TZ).isoformat(),
                "documents": sorted(output, key=lambda x: x["id"]), "failures": failures}
    path = ROOT / "data" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"downloaded": len(output), "bytes": sum(x["bytes"] for x in output),
                      "failures": len(failures)}, ensure_ascii=False), flush=True)
    if len(output) < 20:
        raise SystemExit("有效原始报告不足 20 份，请检查 manifest 中的失败原因并重试")


if __name__ == "__main__":
    main()
