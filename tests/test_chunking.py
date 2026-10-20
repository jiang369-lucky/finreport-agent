import unittest
from types import SimpleNamespace

from finagent.chunking import chunk_page_sources, document_chunks


def line(text, y=100, x=50, right=540, font=10):
    return {"bbox": (x, y, right, y + font), "spans": [{"text": text, "size": font}]}


class Page:
    rect = SimpleNamespace(width=600, height=800)

    def __init__(self, lines):
        self.lines = lines

    def get_text(self, kind, sort=False):
        return {"blocks": [{"lines": [item]} for item in self.lines]}


class Document(list):
    def __init__(self, pages, toc=()):
        super().__init__(Page(page) for page in pages)
        self.toc = toc

    def get_toc(self):
        return self.toc


class ChunkingTests(unittest.TestCase):
    def test_wrapped_lines_and_indented_paragraphs(self):
        doc = Document([[line("第一段的开头", x=70), line("第一段的后续内容。", y=120),
                         line("第二段单独开始。", y=140, x=70)]])
        chunk = list(document_chunks(doc))[0]
        self.assertEqual(chunk["text"], "第一段的开头第一段的后续内容。\n\n第二段单独开始。")

    def test_cross_page_paragraph_removes_running_margins(self):
        doc = Document([
            [line("公司年度报告", y=25), line("公司经营活动的主要变化包括", y=730), line("1", y=775)],
            [line("公司年度报告", y=25), line("产品收入增长和生产效率提升。", y=70), line("2", y=775)],
        ])
        chunk = list(document_chunks(doc))[0]
        self.assertEqual(chunk["text"], "公司经营活动的主要变化包括产品收入增长和生产效率提升。")
        self.assertEqual([source["page"] for source in chunk["sources"]], [1, 2])
        self.assertEqual(chunk["text"][chunk["sources"][1]["start"]:], "产品收入增长和生产效率提升。")

    def test_cross_page_merge_requires_more_than_missing_punctuation(self):
        for previous, following in [
            (line("上一段已经结束。", y=730), line("新段落的内容。", y=70)),
            (line("短行不是可靠续段", y=730, right=180), line("新段落的内容。", y=70)),
            (line("上一页未结束的正文", y=730), line("缩进的新段落。", y=70, x=75)),
            (line("上一页未结束的正文", y=300), line("新段落的内容。", y=70)),
            (line("上一页未结束的正文", y=730), line("营业收入 100 80 20%", y=70)),
            (line("上一页未结束的正文", y=730), line("另一栏的内容。", y=70, x=320)),
        ]:
            with self.subTest(previous=previous, following=following):
                chunk = list(document_chunks(Document([[previous], [following]])))[0]
                self.assertIn("\n\n", chunk["text"])

    def test_repeated_unit_labels_are_not_discarded_as_page_headers(self):
        doc = Document([[line("单位：万元", y=25), line("本页表格正文。", y=100)],
                        [line("单位：万元", y=25), line("下一页表格正文。", y=100)]])
        self.assertEqual(sum(c["text"].count("单位：万元") for c in document_chunks(doc)), 2)

    def test_sections_and_subsections_do_not_share_chunks(self):
        doc = Document([
            [line("第三节 管理层讨论与分析", y=100, font=16), line("一、经营情况", y=140, font=12),
             line("公司经营活动的主要变化包括", y=730)],
            [line("二、风险因素", y=70, font=12), line("公司需要关注市场风险。", y=100)],
        ])
        chunks = list(document_chunks(doc))
        self.assertEqual(chunks[-1]["section_path"], ["第三节 管理层讨论与分析", "二、风险因素"])
        self.assertNotIn("主要变化", chunks[-1]["text"])
        self.assertTrue(all(len({s["page"] for s in c["sources"]}) == 1 for c in chunks))

    def test_bookmarks_identify_headings_without_numbering(self):
        doc = Document([[line("经营回顾"), line("公司业务持续发展。", y=130)]], toc=[[1, "经营回顾", 1]])
        chunks = list(document_chunks(doc))
        self.assertEqual(chunks[0]["section_path"], ["经营回顾"])

    def test_long_paragraph_splits_are_bounded_and_keep_page_offsets(self):
        first = "公司经营情况" * 20
        second = "下一页继续介绍经营变化。" * 20
        doc = Document([[line(first, y=730)], [line(second, y=70)]])
        chunks = list(document_chunks(doc, size=80, overlap=15))
        self.assertTrue(all(len(chunk["text"]) <= 80 for chunk in chunks))
        self.assertEqual("".join(chunk["text"] for chunk in chunks), first + second)
        for chunk in chunks:
            for source in chunk["sources"]:
                self.assertTrue(0 <= source["start"] < source["end"] <= len(chunk["text"]))
                original = first if source["page"] == 1 else second
                self.assertIn(chunk["text"][source["start"]:source["end"]], original)

    def test_empty_pages_do_not_bridge_nonadjacent_pages(self):
        doc = Document([[line("上一页未结束的正文", y=730)], [], [line("另一页内容。", y=70)]])
        chunk = list(document_chunks(doc))[0]
        self.assertIn("\n\n", chunk["text"])
        self.assertEqual([s["page"] for s in chunk["sources"]], [1, 3])

    def test_invalid_size_is_rejected(self):
        for size, overlap in [(0, 0), (10, 10), (10, -1)]:
            with self.subTest(size=size, overlap=overlap), self.assertRaises(ValueError):
                list(document_chunks(Document([]), size, overlap))

    def test_page_sources_do_not_attribute_other_pages_text(self):
        doc = Document([[line("上一页未结束的正文", y=730)], [line("下一页接续的内容。", y=70)]])
        chunk = list(document_chunks(doc))[0]
        sources = chunk_page_sources(chunk)
        self.assertEqual([source["page"] for source in sources], [1, 2])
        self.assertEqual(sources[0]["text"], "上一页未结束的正文")
        self.assertEqual(sources[1]["text"], "下一页接续的内容。")

    def test_short_paragraph_overlap_keeps_sources_and_limit(self):
        doc = Document([[line("甲段落。", y=100), line("乙段落。", y=150), line("丙段落。", y=200)]])
        chunks = list(document_chunks(doc, size=12, overlap=5))
        self.assertEqual([c["text"] for c in chunks], ["甲段落。\n\n乙段落。", "乙段落。\n\n丙段落。"])
        self.assertEqual(chunk_page_sources(chunks[1])[0]["text"], chunks[1]["text"])


if __name__ == "__main__":
    unittest.main()
