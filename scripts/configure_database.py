"""Create local PostgreSQL configuration without printing or overwriting existing secrets."""
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / ".env"
    text = path.read_text("utf-8") if path.exists() else (ROOT / ".env.example").read_text("utf-8")
    values = dict(re.findall(r"^([A-Z_]+)=(.*)$", text, re.M))
    if values.get("DATABASE_URL") and "CHANGE_ME" not in values["DATABASE_URL"]:
        print("PostgreSQL configuration already exists.")
        return
    password = values.get("POSTGRES_PASSWORD", "")
    if not password or password == "CHANGE_ME":
        password = secrets.token_urlsafe(24)
    additions = {
        "POSTGRES_USER": "finagent", "POSTGRES_DB": "finreport",
        "POSTGRES_PASSWORD": password, "FIN_POSTGRES_PORT": "55432",
        "DATABASE_URL": f"postgresql://finagent:{password}@127.0.0.1:55432/finreport",
        "FIN_DATABASE_SCHEMA": "finagent",
    }
    for key, value in additions.items():
        if key in values:
            text = re.sub(r"^" + key + r"=.*$", lambda _: key + "=" + value, text, flags=re.M)
        else:
            text = text.rstrip() + "\n" + key + "=" + value + "\n"
    path.write_text(text, "utf-8")
    print("Local PostgreSQL configuration saved to .env; existing model settings preserved.")


if __name__ == "__main__":
    main()
