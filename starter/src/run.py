"""Pipeline: validate corpus+benchmark, ingest A/B, retrieve A/B/C, evaluate, write audit files.

Run from starter/:  python -m src.run [--skip-ingest]
"""
import csv
import importlib.metadata
import json
import logging
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone

from .chunking import by_paragraph, fixed_size
from .corpus import ROOT, build_manifest, gold_list, load_benchmark, load_corpus, validate_benchmark
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .store import PgVectorStore, content_hash

RESULTS = ROOT / "results"
TOP_K = 3
# Index per strategy. C reuses B's index and only adds the metadata filter at query time.
CONFIGS = {
    "A": {"chunking": "fixed_size", "chunk_size": 500, "overlap": 50, "index": "A", "filter": False},
    "B": {"chunking": "by_paragraph", "chunk_size": 1000, "overlap": 0, "index": "B", "filter": False},
    "C": {"chunking": "by_paragraph", "chunk_size": 1000, "overlap": 0, "index": "B", "filter": True},
}


def make_chunks(pages, index):
    fn = (lambda p: fixed_size(p, 500, 50)) if index == "A" else (lambda p: by_paragraph(p, 1000))
    return [c for p in pages for c in fn(p)]


def ingest(store, emb, pages, index):
    """Embed only new/changed chunks, upsert, prune obsolete. Return stats."""
    t0 = time.time()
    chunks = make_chunks(pages, index)
    old = store.existing_hashes(index)
    todo = [c for c in chunks if old.get(c.chunk_id) != content_hash(c.text)]
    vecs = emb.embed_documents([c.text for c in todo]) if todo else []
    if todo:
        store.upsert(index, todo, vecs)
    removed = store.prune(index, [c.chunk_id for c in chunks], sorted({p.document_id for p in pages}))
    lens = [len(c.text) for c in chunks]
    return {"index": index, "chunks": len(chunks), "embedded_this_run": len(todo), "pruned": removed,
            "rows_in_db": store.count(index), "chars_total": sum(lens), "len_min": min(lens),
            "len_median": statistics.median(lens), "len_mean": round(statistics.mean(lens), 1), "len_max": max(lens),
            "seconds": round(time.time() - t0, 2), "est_tokens_embedded": sum(len(c.text) for c in todo) // 4}


def write_csv(path, rows, cols, keep_existing=False):
    if keep_existing and path.exists():
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def coherence_template(store, pages):
    """10 fixed positions: evenly spaced content pages (sorted), char midpoint of page. Scores left blank."""
    ordered = sorted(pages, key=lambda p: (p.document_id, p.page))
    n = len(ordered)
    picks = [ordered[(2 * i + 1) * n // 20] for i in range(10)]
    rows = []
    for k, p in enumerate(picks, start=1):
        mid = len(p.text) // 2
        for strat, index in (("A", "A"), ("B", "B")):
            for c in make_chunks([p], index):
                if c.metadata["start"] <= mid < c.metadata["end"]:
                    rows.append({"position": f"pos{k:02d}", "strategy": strat, "document_id": p.document_id,
                                 "page": p.page, "mid_offset": mid, "chunk_id": c.chunk_id, "chunk_text": c.text,
                                 "score_0_2": "", "reason": ""})
                    break
    write_csv(RESULTS / "manual_labels" / "coherence_to_label.csv", rows,
              ["position", "strategy", "document_id", "page", "mid_offset", "chunk_id", "chunk_text", "score_0_2", "reason"], keep_existing=True)


def metadata_audit(traces, pages, manifest):
    """Scripted check of source/page/year/topic/section on test top-3 against raw pages and manifest."""
    by_page = {(p.document_id, p.page): p for p in pages}
    docs = {d["source_id"]: d for d in manifest["documents"]}
    rows = []
    for t in traces:
        if t["split"] != "test":
            continue
        for rank, h in enumerate(t["hits"], start=1):
            m, errs = h["metadata"], []
            page = by_page.get((h["document_id"], h["page"]))
            d = docs.get(h["document_id"])
            if not d or h["source"] != d["source_path_or_url"]:
                errs.append("source mismatch")
            if page is None:
                errs.append("page not a content page")
            elif page.text[m["start"]:m["end"]] != h["text"]:
                errs.append("chunk text not at stated page offset")
            if d and (m.get("year") != d["year"] or m.get("topic") != d["topic"]):
                errs.append("year/topic mismatch")
            if not m.get("section"):
                errs.append("section empty")
            rows.append({"strategy": t["strategy"], "question_id": t["id"], "rank": rank, "chunk_id": h["chunk_id"],
                         "document_id": h["document_id"], "page": h["page"], "section": m.get("section", ""),
                         "ok": not errs, "errors": "; ".join(errs)})
    write_csv(RESULTS / "metadata_audit.csv", rows,
              ["strategy", "question_id", "rank", "chunk_id", "document_id", "page", "section", "ok", "errors"])
    return rows


def grounding_template(traces):
    rows = []
    for t in traces:
        if t["split"] != "test":
            continue
        rows.append({"strategy": t["strategy"], "question_id": t["id"], "question": t["question"],
                     "expected_answer": t["expected_answer"], "gold": f"{t['gold_source_id']} p{t['gold_page']}",
                     "evidence_quote": t["evidence_quote"],
                     **{f"top{i}": f"[{h['chunk_id']} p{h['page']}] {h['text']}" for i, h in enumerate(t["hits"], 1)},
                     "score_0_2": "", "reason": ""})
    write_csv(RESULTS / "manual_labels" / "grounding_to_label.csv", rows,
              ["strategy", "question_id", "question", "expected_answer", "gold", "evidence_quote",
               "top1", "top2", "top3", "score_0_2", "reason"], keep_existing=True)


def main() -> None:
    (RESULTS / "manual_labels").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(RESULTS / "run.log", encoding="utf-8")])
    pages, decisions = load_corpus()
    qs = load_benchmark()
    problems = validate_benchmark(pages, qs)
    if problems:
        sys.exit("benchmark/corpus invalid:\n" + "\n".join(problems))
    manifest = build_manifest(pages, decisions)
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(RESULTS / "ingest_page_log.csv", decisions, ["document_id", "page", "kind", "chars"])

    emb = MyEmbedder()
    store = PgVectorStore(os.environ["DATABASE_URL"], emb.dimensions)
    store.create_schema()

    stats_path = RESULTS / "ingest_stats.json"
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else {}
    if "--skip-ingest" not in sys.argv:
        for index in ("A", "B"):
            s = ingest(store, emb, pages, index)
            if s["embedded_this_run"] or index not in stats:
                stats[index] = s
            else:
                stats[index]["last_reingest"] = s
            print("ingest", s)
        stats.setdefault("embedding", {
            "versions": {n: importlib.metadata.version(n) for n in ("psycopg", "google-genai", "pypdf")} |
                        {"python": platform.python_version()}, **{"provider": "Gemini via google-genai (GEMINI_BASE_URL)", "model": emb.model_name,
                              "dimensions": emb.dimensions, "task_type_doc": "RETRIEVAL_DOCUMENT",
                              "task_type_query": "RETRIEVAL_QUERY", "metric": "cosine (exact scan, no ANN index)",
                              "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}})
        stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

    traces = []
    qvec = {q["id"]: emb.embed_query(q["question"]) for q in qs}
    for strat, cfg in CONFIGS.items():
        for q in qs:
            flt = q.get("filter") if cfg["filter"] else None
            hits = store.search(cfg["index"], qvec[q["id"]], TOP_K, flt)
            traces.append({"strategy": strat, "id": q["id"], "split": q["split"], "question": q["question"],
                           "expected_answer": q["expected_answer"], "gold_source_id": q["gold_source_id"],
                           "gold_page": q["gold_page"], "evidence_quote": q["evidence_quote"],
                           "gold_all": gold_list(q), "filter_applied": flt, "hits": hits,
                           "eval": evaluate_query(q, hits)})
    with open(RESULTS / "traces.jsonl", "w", encoding="utf-8") as f:
        for t in traces:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    summary = summarize(traces)
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(RESULTS / "per_question.csv",
              [{"strategy": t["strategy"], "id": t["id"], "split": t["split"], "type": t["eval"]["type"],
                "filter": json.dumps(t["filter_applied"]), "hit1": t["eval"]["hit1"], "hit3": t["eval"]["hit3"],
                "first_hit_rank": t["eval"]["first_hit_rank"], "page_match_top3": t["eval"]["page_match_top3"],
                "top1": f"{t['hits'][0]['document_id']} p{t['hits'][0]['page']}", "gold": f"{t['gold_source_id']} p{t['gold_page']}"}
               for t in traces],
              ["strategy", "id", "split", "type", "filter", "hit1", "hit3", "first_hit_rank", "page_match_top3", "top1", "gold"])
    audit = metadata_audit(traces, pages, manifest)
    print("metadata ok:", sum(r["ok"] for r in audit), "/", len(audit))
    coherence_template(store, pages)
    grounding_template(traces)
    for k, v in sorted(summary.items()):
        print(k, {a: b for a, b in v.items() if b or a == "n"})
    print(f"embedding calls this run: {emb.calls}, chars: {emb.chars}")


if __name__ == "__main__":
    main()
