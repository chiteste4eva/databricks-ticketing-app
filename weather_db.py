"""
Lakebase DDL and helpers for the Day 2 weather vector pipeline.

Tables mirror the ticker_news_documents / ticker_news_embeddings pattern:
  weather_documents  - raw harvested weather text (alerts + forecasts)
  weather_embeddings - chunked, embedded passages for pgvector search

Uses the same LAKEBASE_URL env / Databricks secret resolution as db.py.
"""

import json
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from db import _lakebase_url

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100


@contextmanager
def get_connection():
    """psycopg2 connection to Lakebase (same resolution as db.py)."""
    conn = psycopg2.connect(_lakebase_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_weather_schema():
    """Create pgvector extension, tables, and HNSW index (idempotent)."""
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS weather_documents (
                id TEXT PRIMARY KEY,
                location TEXT NOT NULL,
                source_type TEXT NOT NULL,
                headline TEXT,
                narrative_text TEXT NOT NULL,
                issued_at TIMESTAMPTZ,
                payload JSONB,
                synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS weather_embeddings (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL REFERENCES weather_documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                model_name TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_weather_embeddings_embedding
            ON weather_embeddings
            USING hnsw (embedding vector_cosine_ops);
            """
        )


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding-window chunker over characters (mirrors reference notebook)."""
    chunks = []
    step = size - overlap
    for index, start in enumerate(range(0, len(text), step)):
        piece = text[start:start + size].strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(text):
            break
    return chunks


def upsert_documents(docs: list[dict]) -> int:
    """Upsert normalized weather documents (dedup on id). Returns rows written."""
    if not docs:
        return 0
    rows = [
        (
            d["id"], d["location"], d["source_type"], d.get("headline"),
            d["narrative_text"], d.get("issued_at"),
            json.dumps(d["payload"]) if isinstance(d.get("payload"), dict) else d.get("payload"),
            d.get("synced_at"),
        )
        for d in docs
    ]
    with get_connection() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO weather_documents
                (id, location, source_type, headline, narrative_text, issued_at, payload, synced_at)
            VALUES %s
            ON CONFLICT (id) DO UPDATE SET
                narrative_text = EXCLUDED.narrative_text,
                payload = EXCLUDED.payload,
                synced_at = EXCLUDED.synced_at
            """,
            rows,
            page_size=100,
        )
        return cur.rowcount


def get_unembedded_documents(limit: int = 500) -> list[dict]:
    """Documents with no embeddings yet."""
    with get_connection() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, headline, narrative_text
            FROM weather_documents d
            WHERE NOT EXISTS (
                SELECT 1 FROM weather_embeddings e WHERE e.document_id = d.id
            )
            ORDER BY issued_at DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def insert_embeddings(rows: list[tuple]) -> int:
    """
    Batch-insert (id, document_id, chunk_index, chunk_text, embedding_list, model_name).
    Dedups on id via ON CONFLICT DO NOTHING.
    """
    if not rows:
        return 0
    data = [
        (rid, did, idx, text, "[" + ",".join(f"{x:.6f}" for x in vec) + "]", model)
        for rid, did, idx, text, vec, model in rows
    ]
    with get_connection() as conn, conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO weather_embeddings
                (id, document_id, chunk_index, chunk_text, embedding, model_name)
            VALUES %s
            ON CONFLICT (id) DO NOTHING
            """,
            data,
            template="(%s, %s, %s, %s, %s::vector, %s)",
            page_size=100,
        )
        return cur.rowcount


def search_weather(query_vector: list[float], top_k: int = 5) -> list[dict]:
    """Cosine-similarity search via pgvector's <=> operator."""
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in query_vector) + "]"
    with get_connection() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT d.id, d.location, d.headline, d.narrative_text, e.chunk_text,
                   1 - (e.embedding <=> %s::vector) AS similarity
            FROM weather_embeddings e
            JOIN weather_documents d ON d.id = e.document_id
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
            """,
            (vec_literal, vec_literal, top_k),
        )
        return [dict(r) for r in cur.fetchall()]


def weather_stats() -> dict:
    with get_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM weather_documents")
        docs = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM weather_embeddings")
        embs = cur.fetchone()[0]
        cur.execute("SELECT count(DISTINCT location) FROM weather_documents")
        locs = cur.fetchone()[0]
    return {"documents": docs, "embeddings": embs, "locations": locs}
