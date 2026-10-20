"""Exercise ingestion/retrieval SQL on an isolated SQLite fixture; no production DB."""
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
import asyncio
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

from finagent import corpus
from finagent.chunking import document_chunks
from finagent.harness import Harness
from finagent.storage import SCHEMA


class ChunkingPipelineTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / "work"
        work.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "raw").mkdir()
        self.path = self.root / "raw" / "sample.pdf"
        with pymupdf.open() as pdf:
            for number in (1, 2):
                page = pdf.new_page(width=600, height=800)
                page.insert_text((320, 30), "示例公司年度报告", fontname="china-s", fontsize=8)
                page.insert_text((290, 785), str(number), fontsize=9)
                if number == 1:
                    page.insert_text((50, 120), "第三节 管理层讨论与分析", fontname="china-s", fontsize=14)
                    page.insert_text((50, 740), "公司本期营业收入保持增长，持续推进产品研发与市场拓展，主要经营变化还包括客户结构和业务规模的",
                                     fontname="china-s", fontsize=10)
                else:
                    page.insert_text((50, 70), "调整，公司继续推动主营业务发展并提升经营效率。", fontname="china-s", fontsize=10)
                    page.insert_text((50, 180), "第四节 公司治理", fontname="china-s", fontsize=14)
                    page.insert_text((50, 220), "董事会及股东大会召开情况。", fontname="china-s", fontsize=10)
                    page.insert_text((50, 300), "第五节 股份变动", fontname="china-s", fontsize=14)
                    page.insert_text((50, 340), "股东持股数量。", fontname="china-s", fontsize=10)
            pdf.save(self.path)
        self.record = {"id": "sample_2025H1", "company": "示例公司", "code": "000001",
                       "title": "2025年半年度报告", "period": "2025H1", "published": "2025-08-20",
                       "url": "https://example.com/sample.pdf", "path": "raw/sample.pdf",
                       "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()}
        (self.root / "manifest.json").write_text(json.dumps({"documents": [self.record]}), "utf-8")
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA.split("CREATE TABLE IF NOT EXISTS sessions", 1)[0])

        database = self.db

        class Connection:
            def execute(self, query, parameters=()):
                return database.execute(query.replace("%s", "?"), parameters)

        @contextmanager
        def connect():
            with database:
                yield Connection()

        for name, value in (("connect", connect), ("init_db", lambda: None), ("ROOT", self.root),
                            ("settings", replace(corpus.settings, data_dir=self.root))):
            patcher = patch.object(corpus, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ingest(self):
        with redirect_stdout(io.StringIO()):
            return corpus.ingest_manifest()

    def seed_legacy_index(self):
        r = self.record
        self.db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (r["id"], r["company"], r["code"], r["title"], r["period"], r["published"],
                         r["url"], r["sha256"], r["path"], 2, "fixture"))
        with pymupdf.open(self.path) as pdf:
            for index, page in enumerate(pdf, 1):
                self.db.execute("INSERT INTO pages VALUES(?,?,?)", (r["id"], index, page.get_text()))
        self.db.execute("INSERT INTO chunks VALUES(?,?,?,?,?,?)", ("old", r["id"], 1, 0, "旧片段", "旧 片段"))
        self.db.execute("INSERT INTO metrics VALUES(?,?,?,?,?,?,?,?,?)",
                        ("metric", r["id"], 1, "营业收入", "2025H1", "100", "万元", "保留指标", "candidate"))
        self.db.commit()

    def test_ingestion_retrieval_and_second_page_citation(self):
        report = self.ingest()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["new_documents"], 1)
        index = corpus.Corpus()
        hit = index.search("营业收入", "示例公司", "2025-08-31")[0]
        self.assertEqual(hit["source_pages"], [1, 2])
        self.assertEqual(hit["section_path"], ["第三节 管理层讨论与分析"])
        self.assertIn("业务规模的调整", hit["text"])
        self.assertNotIn("调整，公司", hit["sources"][0]["text"])
        self.assertIn("调整，公司", hit["sources"][1]["text"])
        self.assertEqual(len(hit["sources"][0]["bboxes"]), 2)
        harness = object.__new__(Harness)
        state = {"evidence": {}}
        harness._register({"results": [hit]}, state)
        self.assertEqual(hit["citation_ids"], ["sample_2025H1:p1", "sample_2025H1:p2"])
        self.assertNotIn("调整，公司", state["evidence"]["sample_2025H1:p1"]["excerpt"])
        state.update(status="complete", answer="接续内容见 [sample_2025H1:p2]", repairs=0)
        asyncio.run(harness._review(state))
        self.assertFalse(state["repair_needed"])
        self.assertEqual(state["status"], "complete")

    def test_legacy_reindex_preserves_pages_metrics_and_is_idempotent(self):
        self.seed_legacy_index()
        pages_before = [tuple(row) for row in self.db.execute("SELECT * FROM pages ORDER BY page")]
        report = self.ingest()
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["reindexed_documents"], 1)
        self.assertEqual(report["new_documents"], 0)
        self.assertEqual([tuple(row) for row in self.db.execute("SELECT * FROM pages ORDER BY page")], pages_before)
        self.assertEqual(self.db.execute("SELECT value FROM metrics WHERE id='metric'").fetchone()[0], "100")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM chunk_metadata").fetchone()[0], report["new_chunks"])
        again = self.ingest()
        self.assertEqual((again["new_chunks"], again["reindexed_documents"], again["errors"]), (0, 0, []))

    def test_failed_reindex_rolls_back_old_index(self):
        self.seed_legacy_index()

        def fail_after_first_chunk(pdf):
            yield next(document_chunks(pdf))
            raise ValueError("fixture interruption")

        with patch.object(corpus, "document_chunks", fail_after_first_chunk):
            report = self.ingest()
        self.assertEqual(report["reindexed_documents"], 0)
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(self.db.execute("SELECT text FROM chunks").fetchone()[0], "旧片段")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM chunk_metadata").fetchone()[0], 0)

    def test_legacy_chunks_keep_a_single_page_source(self):
        self.seed_legacy_index()
        index = corpus.Corpus()
        self.assertEqual(index.rows[0]["source_pages"], [1])
        self.assertEqual(index.rows[0]["section_path"], [])


if __name__ == "__main__":
    unittest.main()
