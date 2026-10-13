# Day 2 Homework — Weather Intelligence: Unstructured Data → Lakebase Vector Search → REST API

End-to-end pipeline that harvests unstructured weather text from a public API,
vectorizes it into Lakebase (Postgres + pgvector), and serves semantic
similarity search over a Flask REST endpoint. This extends the Day 1 ticketing
app in the same repo/deployment.

## Chosen data source: NWS API (api.weather.gov)

The National Weather Service API is free, needs no API key, has generous rate
limits, and returns rich unstructured narrative text — exactly what the
assignment recommends:

- `GET /alerts/active?area={state}` — active alerts with `description` and
  `instruction` narrative fields
- `GET /points/{lat},{lon}` → gridpoint lookup → `GET .../forecast` — a
  `detailedForecast` narrative paragraph per forecast period

Why NWS over the alternatives: no key management, no quota anxiety, and the
narrative fields are written by meteorologists (dense, descriptive prose that
embeds well). No justification-heavy multi-source setup needed; the pipeline
is source-agnostic — `weather_client.py` normalizes everything into the same
document schema.

## Schema decisions

`weather_documents` mirrors `ticker_news_documents`:

| column | notes |
|---|---|
| `id TEXT PRIMARY KEY` | stable dedup key — `alert:<nws id>` or `forecast:<sha256(name\|startTime)>` |
| `location TEXT` | as requested, e.g. `Chicago, IL` |
| `source_type TEXT` | `alert` or `forecast` |
| `headline TEXT` | alert event name, or `Period: shortForecast` |
| `narrative_text TEXT` | description+instruction, or detailedForecast |
| `issued_at TIMESTAMPTZ` | `sent`/`effective` (alerts) or `startTime` (forecasts) |
| `payload JSONB` | raw API JSON for provenance |
| `synced_at TIMESTAMPTZ` | ingest time |

`weather_embeddings` mirrors `ticker_news_embeddings`:

| column | notes |
|---|---|
| `id TEXT PRIMARY KEY` | `<document_id>_<chunk_index>` |
| `document_id TEXT` | FK → `weather_documents(id)` |
| `chunk_index INTEGER`, `chunk_text TEXT` | sliding-window chunks |
| `embedding VECTOR(384)` | pgvector column |
| `model_name TEXT` | `sentence-transformers/all-MiniLM-L6-v2` |
| `created_at TIMESTAMPTZ` | |

**Chunking:** sliding window, `CHUNK_SIZE=800` / `CHUNK_OVERLAP=100` characters —
the same values as the reference news pipeline. NWS narratives are a few
hundred to a few thousand characters; 800-char windows keep each chunk
semantically coherent while the 100-char overlap preserves context across
boundaries.

**Model/dimensions:** `sentence-transformers/all-MiniLM-L6-v2` (384-dim) — the
same model as the reference news pipeline, so distance-operator conventions
(`<=>` cosine distance) are shared.

**Index:** HNSW with `vector_cosine_ops` for fast approximate cosine search.

**Dedup:** upserts keyed on stable `id`s — re-syncs never duplicate documents
or embeddings (`ON CONFLICT DO NOTHING` / `DO UPDATE`).

## How to run end-to-end

Prerequisites: `LAKEBASE_URL` env var (or the Databricks `database` /
`lakebase-url` secret, same as Day 1), plus `pip install -r requirements.txt`.

```bash
# 1. Sync weather documents from the NWS API
curl -X POST localhost:8000/weather/sync \
  -H 'Content-Type: application/json' \
  -d '{"locations": ["Chicago, IL", "Austin, TX", "Miami, FL"], "limit": 50}'

# 2. Embed unembedded documents (or: python ingest_weather_embeddings.py)
curl -X POST localhost:8000/weather/embed \
  -H 'Content-Type: application/json' -d '{"limit": 500}'

# 3. Semantic search
curl -X POST localhost:8000/weather/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "flash flood risk this weekend", "top_k": 5}'

# 4. Stats
curl localhost:8000/weather/stats
```

The standalone script `ingest_weather_embeddings.py` runs the same embed step
outside the app (e.g. as a Databricks Job): `LAKEBASE_URL=... python
ingest_weather_embeddings.py --limit 500`.

## Search SQL

Cosine similarity via pgvector's `<=>` operator, exactly as specified:

```sql
SELECT d.id, d.location, d.headline, d.narrative_text, e.chunk_text,
       1 - (e.embedding <=> %s::vector) AS similarity
FROM weather_embeddings e
JOIN weather_documents d ON d.id = e.document_id
ORDER BY e.embedding <=> %s::vector
LIMIT %s;
```

`top_k` is clamped to 1–20; empty/missing queries return 400; an empty table
returns an empty result list.

## Known limitations / improvements

- Geocoding is a built-in table of ~30 major US cities (plus `lat,lon`
  strings) — no external geocoder dependency, but obscure towns won't resolve.
- The embedding model downloads (~90MB) on first use inside the app; a
  pre-warmed model volume would speed cold starts.
- `POST /weather/embed` runs synchronously — fine for homework scale, but a
  background job queue would be better for thousands of documents.
- Stretch ideas: LLM-generated summary of top results (basic RAG),
  `source_type` filter on search, scheduled re-sync of alerts via Databricks
  Jobs, HNSW latency benchmark vs. exact search.
