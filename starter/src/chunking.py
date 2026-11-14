"""Fixed-size and structure-aware chunking; every chunk keeps page, offsets and section."""
import re

from .models import Chunk, DocumentPage


def _section_at(page: DocumentPage, offset: int) -> str:
    section = page.metadata.get("section_start", "")
    for pos, label in page.metadata.get("headings", []):
        if pos <= offset:
            section = label
    return section


def _make(page: DocumentPage, strategy: str, n: int, start: int, end: int) -> Chunk:
    meta = {k: v for k, v in page.metadata.items() if k not in ("headings", "section_start")}
    meta.update(start=start, end=end, section=_section_at(page, start))
    return Chunk(f"{page.document_id}-p{page.page:03d}-{strategy}{n:02d}", page.document_id, page.source,
                 page.page, page.text[start:end], meta)


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Overlapping char windows over page text."""
    step = size - overlap
    return [_make(page, "A", n, s, min(s + size, len(page.text)))
            for n, s in enumerate(range(0, max(len(page.text) - overlap, 1), step))]


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Greedy-pack paragraphs up to max_size; oversized paragraph split on sentence ends."""
    spans, pos = [], 0
    for para in page.text.split("\n\n"):
        spans.append((pos, pos + len(para)))
        pos += len(para) + 2
    pieces = []
    for s, e in spans:
        if e - s <= max_size:
            pieces.append((s, e))
            continue
        cut = s
        for m in re.finditer(r"(?<=[.!?])\s+", page.text[s:e]):
            if s + m.start() - cut >= max_size * 0.6 and s + m.start() - cut <= max_size:
                pieces.append((cut, s + m.start()))
                cut = s + m.end()
        while e - cut > max_size:
            pieces.append((cut, cut + max_size))
            cut += max_size
        pieces.append((cut, e))
    chunks, cur = [], None
    for s, e in pieces:
        if cur and e - cur[0] <= max_size:
            cur = (cur[0], e)
        else:
            if cur:
                chunks.append(cur)
            cur = (s, e)
    if cur:
        chunks.append(cur)
    return [_make(page, "B", n, s, e) for n, (s, e) in enumerate(chunks)]
