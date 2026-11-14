"""Read and normalize source data: RFC plain text (form-feed pages) and PDF."""
import logging
import re
import unicodedata
from pathlib import Path

from .models import DocumentPage

log = logging.getLogger("ingestion")

_FOOTER = re.compile(r"^\S.*\[Page \d+\]\s*$")
_HEADER = re.compile(r"^RFC \d+\s{2,}.+\s{2,}\w+ \d{4}\s*$")
_HEADING = re.compile(r"^(\d+(?:\.\d+)*\.|Appendix [A-Z](?:\.\d+)*\.)\s+(\S.*)$")
_TOC_LINE = re.compile(r"\.{2,}\s*\d+\s*$|(?:\. ){3,}\d+\s*$")
_REFS = re.compile(r"References|Addresses")
_STOP_HEADING = re.compile(r"^Authors?'s? Addresses?$")


def normalize_text(text: str) -> str:
    """NFC, strip RFC page header/footer, dedent, unwrap prose paragraphs, keep literal blocks."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [ln.rstrip() for ln in text.split("\n") if not (_FOOTER.match(ln) or _HEADER.match(ln))]
    blocks, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    out = []
    for b in blocks:
        base = min(len(ln) - len(ln.lstrip()) for ln in b)
        literal = any(len(ln) - len(ln.lstrip()) > base for ln in b[1:]) or len(b) == 1 and base > 3
        stripped = [ln[base:] for ln in b]
        out.append("\n".join(stripped) if literal else re.sub(r"\s+", " ", " ".join(stripped)).strip())
    return "\n\n".join(out)


def page_kind(text: str) -> str:
    """Return 'empty' | 'toc' | 'content'; reference/address pages are decided by caller state."""
    if len(text.strip()) < 200:
        return "empty"
    if sum(bool(_TOC_LINE.search(ln)) for ln in text.split("\n")) >= 6:
        return "toc"
    return "content"


def load_text(path: Path, document_id: str, source: str, metadata: dict) -> tuple[list[DocumentPage], list[dict]]:
    """Split RFC text on form feed. Return (content pages, per-page decision log)."""
    raw_pages = path.read_text(encoding="utf-8").split("\f")
    pages, decisions = [], []
    section = ""
    in_refs = False
    for i, raw in enumerate(raw_pages, start=1):
        text = normalize_text(raw)
        kind = page_kind(text)
        headings, offset = [], 0
        for blk in text.split("\n\n"):
            first = blk.split("\n", 1)[0].strip()
            if (_HEADING.match(first) and len(first) < 90 and first[-1] not in ".:;,") or _STOP_HEADING.match(first):
                headings.append((offset, first))
                in_refs = bool(_REFS.search(first))
            offset += len(blk) + 2
        start_section = section
        if headings:
            section = headings[-1][1]
        if kind == "content" and (in_refs if not headings else all(_REFS.search(h[1]) for h in headings)):
            kind = "references"
        decisions.append({"document_id": document_id, "page": i, "kind": kind, "chars": len(text)})
        if kind != "content":
            log.warning("skip %s p%d: %s (%d chars)", document_id, i, kind, len(text))
            continue
        pages.append(DocumentPage(document_id, source, i, text,
                                  {**metadata, "headings": headings, "section_start": start_section}))
    return pages, decisions


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract each physical page via pypdf; empty pages logged and skipped."""
    from pypdf import PdfReader

    pages = []
    for i, p in enumerate(PdfReader(str(path)).pages, start=1):
        text = normalize_text(p.extract_text() or "")
        if not text:
            log.warning("empty page %s p%d (OCR needed?)", document_id, i)
            continue
        pages.append(DocumentPage(document_id, source, i, text, {"headings": [], "section_start": ""}))
    return pages
