from finagent.corpus import ingest_manifest
import json

if __name__ == "__main__":
    print(json.dumps(ingest_manifest(), ensure_ascii=False, indent=2))
