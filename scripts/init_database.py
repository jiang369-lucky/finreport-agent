"""Initialize PostgreSQL and import the existing local dataset once."""
from finagent.config import settings
from finagent.storage import connect, init_db


def main():
    init_db()
    source = settings.data_dir / "research.sqlite3"
    with connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS value FROM documents").fetchone()["value"]
    if count == 0 and source.exists():
        from .migrate_sqlite import migrate
        migrate(source)
    print("PostgreSQL schema and dataset are ready.")


if __name__ == "__main__":
    main()
