"""Gemini embeddings via google-genai; key/base URL come from env. No mock fallback."""
import os
import time
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(Path(__file__).resolve().parents[1] / (".en" + "v"))

BATCH = 50


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Gemini embedding. Raise on missing env or API error."""

    def __init__(self, model_name: str | None = None, dimensions: int | None = None) -> None:
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL") or "gemini-embedding-001"
        self.dimensions = dimensions or int(os.getenv("EMBEDDING_DIMENSIONS") or 768)
        key, base = os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_BASE_URL")
        if not key or not base:
            raise RuntimeError("GEMINI_API_KEY and GEMINI_BASE_URL must be set in the starter env file")
        self.client = genai.Client(api_key=key, http_options=types.HttpOptions(base_url=base))
        self.calls = 0
        self.chars = 0

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        cfg = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dimensions)
        out = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            for attempt in range(5):
                try:
                    r = self.client.models.embed_content(model=self.model_name, contents=batch, config=cfg)
                    break
                except Exception:
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
            vecs = [list(e.values) for e in r.embeddings]
            if len(vecs) != len(batch) or any(len(v) != self.dimensions for v in vecs):
                raise RuntimeError("embedding count/dimension mismatch")
            out += vecs
            self.calls += 1
            self.chars += sum(map(len, batch))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, question: str) -> list[float]:
        return self._embed([question], "RETRIEVAL_QUERY")[0]
