"""Score retrieved evidence per query; aggregate Hit@1 / Evidence Hit@3."""
from collections import defaultdict
from typing import Any

from .corpus import DOCS, gold_list, squash


def _is_hit(hit: dict[str, Any], golds: list[dict]) -> bool:
    t = squash(hit["text"])
    return any(hit["document_id"] == g["source_id"] and hit["page"] == g["page"] and squash(g["evidence_quote"]) in t
               for g in golds)


def _page_match(hit: dict[str, Any], golds: list[dict]) -> bool:
    return any(hit["document_id"] == g["source_id"] and hit["page"] == g["page"] for g in golds)


def _filter_ok(hit: dict[str, Any], flt: dict) -> bool:
    k, v = next(iter(flt.items()))
    return hit["document_id"] == v if k == "document_id" else DOCS[hit["document_id"]][k] == v


def evaluate_query(question: dict[str, Any], hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Hit = same source+page and chunk contains the whole evidence quote (any accepted gold)."""
    golds = gold_list(question)
    flags = [_is_hit(h, golds) for h in hits]
    flt = question.get("filter")
    return {
        "id": question["id"], "split": question["split"], "type": question["type"], "filter": flt,
        "hit1": bool(flags and flags[0]), "hit3": any(flags[:3]),
        "first_hit_rank": next((i + 1 for i, f in enumerate(flags) if f), None),
        "page_match_top3": any(_page_match(h, golds) for h in hits[:3]),
        "filter_compliant": None if not flt else all(_filter_ok(h, flt) for h in hits[:3]),
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts per strategy and split (dev/test/all)."""
    acc = defaultdict(lambda: {"n": 0, "hit1": 0, "hit3": 0, "page_match_top3": 0, "filter_n": 0, "filter_ok": 0})
    for t in traces:
        for split in (t["split"], "all"):
            a = acc[f"{t['strategy']}|{split}"]
            a["n"] += 1
            a["hit1"] += t["eval"]["hit1"]
            a["hit3"] += t["eval"]["hit3"]
            a["page_match_top3"] += t["eval"]["page_match_top3"]
            if t["eval"]["filter_compliant"] is not None:
                a["filter_n"] += 1
                a["filter_ok"] += t["eval"]["filter_compliant"]
    return dict(acc)
