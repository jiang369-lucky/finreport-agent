"""Verify every installed lock release existed before the project's historical cutoff."""
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = datetime.fromisoformat("2025-08-31T15:59:59+00:00")


def main():
    lock = ROOT / "requirements.lock"
    packages = re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", lock.read_text("utf-8"), re.M)
    records = []
    for name, version in packages:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=40) as response:
            info = json.load(response)
        timestamps = [datetime.fromisoformat(x["upload_time_iso_8601"].replace("Z", "+00:00")) for x in info["urls"]]
        earliest = min(timestamps) if timestamps else datetime.max.replace(tzinfo=timezone.utc)
        records.append({"name": name, "version": version, "first_upload": earliest.isoformat(), "allowed": earliest <= CUTOFF})
    output = ROOT / "data" / "dependency-audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"cutoff": "2025-08-31 Asia/Shanghai", "packages": records}, ensure_ascii=False, indent=2), "utf-8")
    bad = [x for x in records if not x["allowed"]]
    print(json.dumps({"packages": len(records), "violations": bad}, ensure_ascii=False))
    return bool(bad)


if __name__ == "__main__":
    sys.exit(main())
