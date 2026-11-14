"""Corpus definition, manifest and benchmark validation."""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .ingestion import load_text
from .models import DocumentPage

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "starter" / "data" / "raw"
BENCH = ROOT / "benchmark" / "questions.json"

LICENSE = ("Copyright (c) IETF Trust and the persons identified as the document authors; RFC text may be "
           "reproduced under the IETF Trust Legal Provisions (BCP 78) as noted in each document's header.")

# year parsed from each RFC header date; topic is a fixed label chosen by the author
DOCS = {
    "rfc6749": {"title": "The OAuth 2.0 Authorization Framework", "year": 2012, "topic": "oauth2-framework"},
    "rfc7519": {"title": "JSON Web Token (JWT)", "year": 2015, "topic": "jwt"},
    "rfc7636": {"title": "Proof Key for Code Exchange by OAuth Public Clients", "year": 2015, "topic": "pkce"},
}


def load_corpus() -> tuple[list[DocumentPage], list[dict]]:
    pages, decisions = [], []
    for doc_id, meta in DOCS.items():
        path = RAW / f"{doc_id}.txt"
        p, d = load_text(path, doc_id, f"https://www.rfc-editor.org/rfc/{doc_id}.txt",
                         {"title": meta["title"], "year": meta["year"], "topic": meta["topic"]})
        pages += p
        decisions += d
    return pages, decisions


def build_manifest(pages: list[DocumentPage], decisions: list[dict]) -> dict:
    docs = []
    for doc_id, meta in DOCS.items():
        path = RAW / f"{doc_id}.txt"
        mine = [p for p in pages if p.document_id == doc_id]
        skipped = [{"page": d["page"], "reason": d["kind"]} for d in decisions
                   if d["document_id"] == doc_id and d["kind"] != "content"]
        docs.append({
            "source_id": doc_id, "title": meta["title"], "year": meta["year"], "topic": meta["topic"],
            "source_path_or_url": f"https://www.rfc-editor.org/rfc/{doc_id}.txt",
            "local_path": f"starter/data/raw/{doc_id}.txt",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "license_or_permission": LICENSE,
            "accessed_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "physical_pages": sum(1 for d in decisions if d["document_id"] == doc_id and d["chars"] >= 0),
            "content_pages": [p.page for p in mine], "content_page_count": len(mine),
            "content_chars": sum(len(p.text) for p in mine), "skipped_pages": skipped,
        })
    return {
        "page_counting_rule": ("physical page = text between form-feed characters in the RFC plain text; content page = "
                               "not empty (<200 chars), not table of contents, not references/authors' addresses"),
        "total_content_pages": sum(d["content_page_count"] for d in docs),
        "documents": docs,
    }


def squash(s: str) -> str:
    return " ".join(s.split())


def load_benchmark() -> list[dict]:
    return json.loads(BENCH.read_text(encoding="utf-8"))


def gold_list(q: dict) -> list[dict]:
    """All accepted (source,page,quote) triples."""
    return [{"source_id": q["gold_source_id"], "page": q["gold_page"], "evidence_quote": q["evidence_quote"]},
            *q.get("alt_gold", [])]


def validate_benchmark(pages: list[DocumentPage], qs: list[dict]) -> list[str]:
    """Return problems: missing/duplicate-evidence, split/filter minimums."""
    text = {(p.document_id, p.page): squash(p.text) for p in pages}
    errs = []
    for q in qs:
        for g in gold_list(q):
            t = text.get((g["source_id"], g["page"]))
            if t is None or squash(g["evidence_quote"]) not in t:
                errs.append(f"{q['id']}: evidence not found on {g['source_id']} p{g['page']}")
        if q.get("filter"):
            meta = DOCS[q["gold_source_id"]]
            key, val = next(iter(q["filter"].items()))
            actual = q["gold_source_id"] if key == "document_id" else meta[key]
            if actual != val:
                errs.append(f"{q['id']}: filter excludes gold")
    n_dev = sum(q["split"] == "dev" for q in qs)
    n_test = sum(q["split"] == "test" for q in qs)
    if len(qs) < 20 or n_dev < 15 or n_test < 5:
        errs.append(f"size: {len(qs)} total, {n_dev} dev, {n_test} test")
    if sum(bool(q.get("filter")) for q in qs) < 3 or not any(q.get("filter") and q["split"] == "test" for q in qs):
        errs.append("need >=3 filter questions, >=1 in test")
    if sum(len(p.text) for p in pages) and len(pages) < 30:
        errs.append(f"corpus has {len(pages)} content pages (<30)")
    return errs
