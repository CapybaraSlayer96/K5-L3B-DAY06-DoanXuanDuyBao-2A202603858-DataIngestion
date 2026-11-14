"""PostgreSQL + pgvector store: exact cosine search, SQL metadata filters, idempotent upsert."""
import hashlib
import json
from typing import Any

import psycopg

from .models import Chunk

FILTER_COLS = {"document_id", "year", "topic"}


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in v) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int, table: str = "chunks") -> None:
        self.dimensions = dimensions
        self.table = table
        self.conn = psycopg.connect(database_url, autocommit=True)

    def create_schema(self) -> None:
        self.conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.table} (
                strategy text NOT NULL, chunk_id text NOT NULL, document_id text NOT NULL,
                source text NOT NULL, page int NOT NULL, section text, year int, topic text,
                text text NOT NULL, content_hash text NOT NULL, metadata jsonb NOT NULL,
                embedding vector({self.dimensions}) NOT NULL, PRIMARY KEY (strategy, chunk_id))""")
        self.conn.execute(f"CREATE INDEX IF NOT EXISTS {self.table}_filter ON {self.table} (strategy, document_id, year)")

    def existing_hashes(self, strategy: str) -> dict[str, str]:
        rows = self.conn.execute(f"SELECT chunk_id, content_hash FROM {self.table} WHERE strategy=%s", (strategy,))
        return dict(rows.fetchall())

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Upsert by (strategy, chunk_id). Obsolete chunks removed via prune()."""
        if len(chunks) != len(vectors) or any(len(v) != self.dimensions for v in vectors):
            raise ValueError("chunk/vector count or dimension mismatch")
        with self.conn.transaction():
            for c, v in zip(chunks, vectors):
                self.conn.execute(
                    f"""INSERT INTO {self.table} VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                       ON CONFLICT (strategy, chunk_id) DO UPDATE SET text=EXCLUDED.text, source=EXCLUDED.source,
                       page=EXCLUDED.page, section=EXCLUDED.section, year=EXCLUDED.year, topic=EXCLUDED.topic,
                       content_hash=EXCLUDED.content_hash, metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding""",
                    (strategy, c.chunk_id, c.document_id, c.source, c.page, c.metadata.get("section"),
                     c.metadata.get("year"), c.metadata.get("topic"), c.text, content_hash(c.text),
                     json.dumps(c.metadata), _vec(v)))

    def prune(self, strategy: str, keep_ids: list[str], document_ids: list[str]) -> int:
        """Delete chunks of given documents that are no longer produced."""
        cur = self.conn.execute(
            f"DELETE FROM {self.table} WHERE strategy=%s AND document_id = ANY(%s) AND NOT (chunk_id = ANY(%s))",
            (strategy, document_ids, keep_ids))
        return cur.rowcount

    def count(self, strategy: str) -> int:
        return self.conn.execute(f"SELECT count(*) FROM {self.table} WHERE strategy=%s", (strategy,)).fetchone()[0]

    def search(self, strategy: str, query_vector: list[float], top_k: int = 3,
               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Cosine top-k; filter fields limited to FILTER_COLS, values parameterized."""
        where, params = ["strategy=%s"], [strategy]
        for k, v in (filters or {}).items():
            if k not in FILTER_COLS:
                raise ValueError(f"filter field not allowed: {k}")
            where.append(f"{k}=%s")
            params.append(v)
        q = _vec(query_vector)
        sql = (f"SELECT chunk_id, text, 1-(embedding <=> %s::vector) AS score, document_id, source, page, metadata "
               f"FROM {self.table} WHERE {' AND '.join(where)} ORDER BY embedding <=> %s::vector LIMIT %s")
        rows = self.conn.execute(sql, [q, *params, q, top_k]).fetchall()
        return [{"chunk_id": r[0], "text": r[1], "score": float(r[2]), "document_id": r[3], "source": r[4],
                 "page": r[5], "metadata": r[6]} for r in rows]
