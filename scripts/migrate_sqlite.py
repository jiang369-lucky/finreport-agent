"""One-way, transactional import into an empty PostgreSQL schema; SQLite stays read-only."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from psycopg import sql
from psycopg.rows import tuple_row

from finagent.config import ROOT, settings
from finagent.storage import connect, dump, init_db, now

TABLES = ("documents", "pages", "chunks", "metrics", "sessions", "turns", "summaries", "runs", "artifacts")
KEYS = {"pages": ("document_id", "page"), "summaries": ("session_id", "as_of")}


def _digest(cursor):
    digest = hashlib.sha256()
    while batch := cursor.fetchmany(500):
        for row in batch:
            digest.update(dump(list(row)).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def migrate(source: Path):
    source = source.resolve()
    if not source.is_file():
        raise ValueError("SQLite source file does not exist")
    init_db()
    report = {"source": str(source), "target_schema": settings.database_schema,
              "status": "verified", "tables": {}, "recorded_at": now()}
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as old, connect() as new:
        names = sql.SQL(",").join(map(sql.Identifier, TABLES))
        new.execute(sql.SQL("LOCK TABLE {} IN EXCLUSIVE MODE").format(names))
        for table in TABLES:
            count = new.execute(sql.SQL("SELECT COUNT(*) AS value FROM {}").format(sql.Identifier(table))).fetchone()["value"]
            if count:
                raise ValueError("PostgreSQL target schema is not empty; refusing to overwrite: " + table)
        for table in TABLES:
            columns = [x[1] for x in old.execute(f'PRAGMA table_info("{table}")')]
            if not columns:
                raise ValueError("Missing SQLite source table: " + table)
            preserved = ["seq", *columns] if table in ("turns", "runs") else columns
            selection = "rowid AS seq,*" if table in ("turns", "runs") else "*"
            source_rows = old.execute(f'SELECT {selection} FROM "{table}" ORDER BY rowid')
            insert = sql.SQL("COPY {} ({}) FROM STDIN").format(
                sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, preserved)))
            count = 0
            with new.cursor() as cursor, cursor.copy(insert) as copy:
                while batch := source_rows.fetchmany(500):
                    for row in batch:
                        copy.write_row(row)
                    count += len(batch)
            key = KEYS.get(table, ("id",))
            order = ",".join('"' + x + '"' for x in key)
            source_digest = _digest(old.execute(
                f'SELECT {("rowid AS seq," if table in ("turns","runs") else "")}'
                + ",".join('"' + x + '"' for x in columns)
                + f' FROM "{table}" ORDER BY {order}'))
            with new.cursor(row_factory=tuple_row) as cursor:
                cursor.execute(sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
                    sql.SQL(",").join(map(sql.Identifier, preserved)), sql.Identifier(table),
                    sql.SQL(",").join(map(sql.Identifier, key))))
                target_digest = _digest(cursor)
            target_count = new.execute(
                sql.SQL("SELECT COUNT(*) AS value FROM {}").format(sql.Identifier(table))).fetchone()["value"]
            if source_digest != target_digest or count != target_count:
                raise ValueError("Migration verification failed: " + table)
            report["tables"][table] = {"rows": count, "sha256": source_digest, "verified": True}
        for table in ("turns", "runs"):
            new.execute(sql.SQL(
                "SELECT setval(pg_get_serial_sequence(%s,'seq'),"
                "GREATEST(COALESCE(MAX(seq),1),1),COUNT(*)>0) FROM {}"
            ).format(sql.Identifier(table)), (table,))
    output = ROOT / "data" / "postgres-migration.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps({"status": report["status"], "rows": {k: v["rows"] for k, v in report["tables"].items()}}, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=settings.data_dir / "research.sqlite3")
    migrate(parser.parse_args().source)


if __name__ == "__main__":
    main()
