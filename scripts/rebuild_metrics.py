import json
import pymupdf
from finagent.corpus import metric_lines, extract_metrics
from finagent.storage import connect

def main():
    total = 0
    with connect() as conn:
        documents = [dict(x) for x in conn.execute("SELECT * FROM documents ORDER BY id")]
    for document in documents:
        with pymupdf.open(document["path"]) as pdf, connect() as conn:
            conn.execute("DELETE FROM metrics WHERE document_id=%s", (document["id"],))
            count = 0
            if document["period"].isdigit():
                for index in range(min(18,len(pdf))):
                    raw = pdf[index].get_text()
                    compact = "".join(raw.split())
                    if not any(k in compact for k in ("主要会计数据","营业收入","营业总收入")):
                        continue
                    extracted = extract_metrics(metric_lines(pdf[index]),document["period"],index+1)
                    for ordinal, metric in enumerate(extracted):
                        conn.execute("INSERT INTO metrics VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (f"{document['id']}:p{index+1}:m{ordinal}",document["id"],index+1,
                             metric["label"],metric["period"],metric["value"],metric["unit"],metric["excerpt"],metric["confidence"]))
                        count += 1
            total += count
        print(document["id"],count,flush=True)
    print(json.dumps({"metrics":total,"extractor_version":2}))

if __name__ == "__main__":
    main()
