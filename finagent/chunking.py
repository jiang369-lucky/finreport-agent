"""Section-aware PDF paragraphs and bounded chunks with page-level provenance."""
from collections import Counter
import re


_NUMBERING = (
    (1, r"^第[一二三四五六七八九十百零〇\d]+[章节篇部]"),
    (2, r"^[一二三四五六七八九十百]+[、．.]"),
    (3, r"^[（(][一二三四五六七八九十百]+[）)]"),
    (4, r"^(?:[（(]\d{1,2}[）)]|\d{1,2}[、．.](?!\d))"),
)
_END = re.compile(r"[。！？!?；;：:][”’\"')）]*$")


def _key(text):
    return re.sub(r"\s+", "", text)


def _lines(document):
    pages = []
    margins = Counter()
    for number, page in enumerate(document, 1):
        lines = []
        margin_keys = set()
        for block_index, block in enumerate(page.get_text("dict", sort=True)["blocks"]):
            for line in block.get("lines", []):
                spans = [span for span in line["spans"] if span["text"].strip()]
                if not spans:
                    continue
                text = "".join(span["text"] for span in line["spans"]).replace("\x00", "").strip()
                sizes = Counter()
                for span in spans:
                    sizes[round(span["size"], 1)] += len(span["text"].strip())
                font = sizes.most_common(1)[0][0]
                bbox = tuple(line["bbox"])
                margin = bbox[1] < page.rect.height * .08 or bbox[3] > page.rect.height * .94
                margin_key = re.sub(r"\d+", "#", _key(text))
                if margin:
                    margin_keys.add(margin_key)
                numeric = sum(c.isdigit() for c in text) / max(len(text), 1)
                lines.append({"text": text, "bbox": bbox, "font": font, "page": number,
                              "width": page.rect.width, "height": page.rect.height,
                              "block": block_index, "margin": margin, "margin_key": margin_key,
                              "table": numeric > .3 or not re.search(r"[A-Za-z\u4e00-\u9fff]", text)})
        margins.update(margin_keys)
        pages.append(lines)
    repeated = {key for key, count in margins.items() if count >= max(2, len(pages) * .4)}
    for lines in pages:
        for line in lines:
            footer = line["bbox"][1] > line["height"] * .85 and re.fullmatch(
                r"(?:第\s*)?\d+(?:\s*[/／]\s*\d+)?\s*页?", line["text"])
            if footer or (line["margin"] and line["margin_key"] in repeated
                          and not re.search(r"单位|币种", line["text"])):
                continue
            yield line


def _heading(line, bookmarks, body_font):
    text = _key(line["text"])
    if len(text) > 90 or re.search(r"[。！？!?；;，,]", text) or line["table"]:
        return 0
    if (line["page"], text) in bookmarks:
        return bookmarks[line["page"], text]
    for level, pattern in _NUMBERING:
        if re.match(pattern, text):
            return level
    if len(text) < 50 and line["font"] >= body_font * 1.35:
        return 2
    return 0


def _continues(previous, line):
    if previous["table"] or line["table"]:
        return False
    if abs(previous["font"] - line["font"]) > max(1.5, previous["font"] * .25):
        return False
    left = line["bbox"][0] - previous["bbox"][0]
    if left > line["font"] * .8 or abs(left) > line["font"] * 2.5:
        return False
    if line["page"] == previous["page"]:
        gap = line["bbox"][1] - previous["bbox"][3]
        if not -.5 <= gap <= line["font"] * 1.5:
            return False
        return (previous["block"] == line["block"] or not _END.search(previous["text"])
                or previous["bbox"][2] > previous["width"] * .85)
    return (line["page"] == previous["page"] + 1
            and not _END.search(previous["text"])
            and previous["bbox"][3] > previous["height"] * .72
            and previous["bbox"][2] > previous["width"] * .7
            and line["bbox"][1] < line["height"] * .25)


def _paragraphs(document):
    lines = list(_lines(document))
    fonts = Counter()
    for line in lines:
        if not line["table"]:
            fonts[line["font"]] += len(line["text"])
    body_font = fonts.most_common(1)[0][0] if fonts else 10
    bookmarks = {(page, _key(title)): level for level, title, page in document.get_toc() if page > 0}
    section = []
    pending = None
    previous = None
    for line in lines:
        level = _heading(line, bookmarks, body_font)
        if level:
            if pending:
                yield pending
                pending = None
            while section and section[-1][0] >= level:
                section.pop()
            section.append((level, line["text"]))
        continued = not level and pending and previous and _continues(previous, line)
        if not continued:
            if pending:
                yield pending
            pending = {"text": "", "section_path": [title for _, title in section], "sources": []}
        separator = " " if (pending["text"] and re.search(r"[A-Za-z0-9]$", pending["text"])
                            and re.match(r"[A-Za-z0-9]", line["text"])) else ""
        start = len(pending["text"]) + len(separator)
        pending["text"] += separator + line["text"]
        pending["sources"].append({"page": line["page"], "bbox": list(line["bbox"]),
                                   "start": start, "end": len(pending["text"])})
        if level:
            yield pending
            pending = None
            previous = None
        else:
            previous = line
    if pending:
        yield pending


def _slice(paragraph, start, end):
    sources = [{**source, "start": max(source["start"], start) - start,
                "end": min(source["end"], end) - start}
               for source in paragraph["sources"] if source["start"] < end and source["end"] > start]
    return {"text": paragraph["text"][start:end], "section_path": paragraph["section_path"],
            "sources": sources}


def _parts(paragraph, size):
    start = 0
    text = paragraph["text"]
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundaries = [m.end() for m in re.finditer(r"[。！？!?；;]\s*|\s+", text[start:end])]
            if boundaries and boundaries[-1] >= size // 2:
                end = start + boundaries[-1]
        yield _slice(paragraph, start, end)
        start = end


def _chunk(paragraphs):
    text = ""
    sources = []
    for paragraph in paragraphs:
        if text:
            text += "\n\n"
        offset = len(text)
        sources.extend({**source, "start": source["start"] + offset, "end": source["end"] + offset}
                       for source in paragraph["sources"])
        text += paragraph["text"]
    return {"text": text, "page": sources[0]["page"],
            "section_path": paragraphs[0]["section_path"], "sources": sources}


def document_chunks(document, size=1100, overlap=140):
    """Keep section/paragraph boundaries; only long paragraphs need sentence/length splits.

    Sources carry offsets into chunk text and original PDF line boxes. Overlap retains
    complete short paragraphs only, never borrowing text from a different section.
    """
    if size < 1 or not 0 <= overlap < size:
        raise ValueError("切分长度必须为正数，重叠长度须小于切分长度")
    pending = []
    length = 0
    for paragraph in _paragraphs(document):
        for part in _parts(paragraph, size):
            changed_section = pending and part["section_path"] != pending[0]["section_path"]
            if pending and (changed_section or length + 2 + len(part["text"]) > size):
                yield _chunk(pending)
                retained = []
                retained_length = 0
                if not changed_section:
                    for previous in reversed(pending):
                        added = len(previous["text"]) + (2 if retained else 0)
                        if retained_length + added > overlap:
                            break
                        retained.insert(0, previous)
                        retained_length += added
                while retained and retained_length + 2 + len(part["text"]) > size:
                    retained_length -= len(retained.pop(0)["text"]) + (2 if retained else 0)
                pending = retained
                length = retained_length
            length += len(part["text"]) + (2 if pending else 0)
            pending.append(part)
    if pending:
        yield _chunk(pending)


def chunk_page_sources(chunk):
    """Return only the text belonging to each physical page, for accurate citations."""
    grouped = {}
    for source in chunk.get("sources", []):
        grouped.setdefault(source["page"], []).append(source)
    if not grouped:
        return [{"page": chunk["page"], "text": chunk["text"], "bboxes": []}]
    return [{"page": page,
             "text": chunk["text"][min(s["start"] for s in spans):max(s["end"] for s in spans)],
             "bboxes": [s["bbox"] for s in spans]}
            for page, spans in grouped.items()]
