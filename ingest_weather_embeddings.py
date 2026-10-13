#!/usr/bin/env python3
"""
Ingest weather documents -> vector embeddings (Lakebase + pgvector).

Plain-Python batch job (no Spark JDBC writes - unreliable against Lakebase):
  1. Reads unembedded rows from weather_documents via psycopg2.
  2. Chunks narrative_text with a sliding window (800 chars, 100 overlap).
  3. Embeds each chunk with sentence-transformers/all-MiniLM-L6-v2 (384-dim).
  4. Writes vectors into weather_embeddings with batched INSERT ... ON CONFLICT.

Usage:
    LAKEBASE_URL='postgresql://...' python ingest_weather_embeddings.py [--limit 500]

The same logic is exposed by the Flask app's POST /weather/embed endpoint so
the pipeline can also run inside the deployed Databricks App.
"""

import argparse
import os
import sys

os.environ.setdefault("HF_HOME", "/tmp/.cache/huggingface")
os.environ.setdefault("TRANSFORMERS_CACHE", "/tmp/.cache/huggingface")
os.environ.setdefault("HF_HUB_CACHE", "/tmp/.cache/huggingface")

from weather_db import (
    EMBEDDING_MODEL,
    chunk_text,
    get_unembedded_documents,
    init_weather_schema,
    insert_embeddings,
)


def run(limit: int = 500) -> dict:
    from sentence_transformers import SentenceTransformer

    init_weather_schema()

    docs = get_unembedded_documents(limit=limit)
    print(f"Found {len(docs)} unembedded documents")
    if not docs:
        return {"documents": 0, "chunks": 0, "inserted": 0}

    print(f"Loading embedding model {EMBEDDING_MODEL} ...")
    model = SentenceTransformer(EMBEDDING_MODEL)

    rows: list[tuple] = []
    batch_size = 32
    pending = []  # (doc_id, chunk_index, chunk_text)

    def flush():
        if not pending:
            return
        texts = [t for _, _, t in pending]
        vectors = model.encode(texts, show_progress_bar=False, batch_size=batch_size)
        for (doc_id, chunk_index, chunk_text_), vec in zip(pending, vectors):
            emb_id = f"{doc_id}_{chunk_index}"
            rows.append((emb_id, doc_id, chunk_index, chunk_text_, list(vec), EMBEDDING_MODEL))
        pending.clear()

    for doc in docs:
        for chunk_index, chunk in enumerate(chunk_text(doc["narrative_text"] or "")):
            pending.append((doc["id"], chunk_index, chunk))
            if len(pending) >= 128:
                flush()
    flush()

    inserted = insert_embeddings(rows)
    print(f"Embedded {len(docs)} documents -> {len(rows)} chunks, inserted {inserted}")
    return {"documents": len(docs), "chunks": len(rows), "inserted": inserted}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()
    try:
        result = run(limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - surface a clean error for job logs
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    print(result)
