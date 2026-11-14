"""Auto chunk-boundary metric: share of chunks that start at a paragraph/sentence start and end at a sentence end."""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)

from src.corpus import ROOT, load_corpus  # noqa: E402
from src.run import make_chunks  # noqa: E402

pages, _ = load_corpus()
by_page = {(p.document_id, p.page): p.text for p in pages}
out = {}
for index in ("A", "B"):
    chunks = make_chunks(pages, index)
    clean_start = clean_end = 0
    for c in chunks:
        text = by_page[(c.document_id, c.page)]
        s, e = c.metadata["start"], c.metadata["end"]
        clean_start += s == 0 or text[s - 2:s] == "\n\n" or text[s - 1] in ".!?\n" or text[s - 2:s - 1] in ".!?"
        clean_end += e == len(text) or text[e - 1] in ".!?:" or text[e:e + 2] == "\n\n"
    out[index] = {"chunks": len(chunks), "clean_start": clean_start, "clean_end": clean_end,
                  "both_clean_share_upper_bound": round(min(clean_start, clean_end) / len(chunks), 3)}
(ROOT / "results" / "boundary_metric.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(out)
