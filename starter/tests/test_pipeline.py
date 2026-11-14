"""Run from starter/: python -m pytest tests -q (needs the pgvector container for the store test)."""
import logging
import os

from src import embeddings  # noqa: F401  (loads env)
from src.chunking import by_paragraph, fixed_size
from src.corpus import load_benchmark, load_corpus, validate_benchmark
from src.evaluation import evaluate_query
from src.ingestion import normalize_text
from src.models import Chunk

logging.disable(logging.CRITICAL)


def test_normalize_strips_page_furniture_and_unwraps():
    raw = "Hardt                        Standards Track                    [Page 5]\n\n   line one\n   line two\n"
    assert normalize_text(raw) == "line one line two"


def test_chunk_offsets_roundtrip():
    pages, _ = load_corpus()
    for p in pages[:20]:
        for c in fixed_size(p, 500, 50) + by_paragraph(p, 1000):
            assert p.text[c.metadata["start"]:c.metadata["end"]] == c.text
            assert c.page == p.page and c.document_id == p.document_id


def test_corpus_and_benchmark_valid():
    pages, _ = load_corpus()
    assert len(pages) >= 30
    assert validate_benchmark(pages, load_benchmark()) == []


def test_hit_requires_full_evidence_on_gold_page():
    q = {"id": "x", "split": "dev", "type": "t", "gold_source_id": "d", "gold_page": 2, "evidence_quote": "must be 5"}
    ok = {"document_id": "d", "page": 2, "text": "it  must\nbe 5 days"}
    split = {"document_id": "d", "page": 2, "text": "it must"}
    wrong_page = {"document_id": "d", "page": 3, "text": "must be 5"}
    assert evaluate_query(q, [ok])["hit1"]
    assert not evaluate_query(q, [split, wrong_page])["hit3"]


def test_store_upsert_idempotent_and_prune():
    from src.store import PgVectorStore
    store = PgVectorStore(os.environ["DATABASE_URL"], 4, table="chunks_test")
    store.conn.execute("DROP TABLE IF EXISTS chunks_test")
    store.create_schema()
    chunks = [Chunk(f"t{i}", "doc-test", "s", 1, f"text {i}", {"year": 2000}) for i in range(3)]
    vecs = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]]
    try:
        for _ in range(2):
            store.upsert("TEST", chunks, vecs)
        assert store.count("TEST") == 3
        assert store.prune("TEST", ["t0"], ["doc-test"]) == 2 and store.count("TEST") == 1
        hits = store.search("TEST", [1.0, 0, 0, 0], 3, {"year": 2000})
        assert [h["chunk_id"] for h in hits] == ["t0"]
    finally:
        store.conn.execute("DROP TABLE IF EXISTS chunks_test")
