"""
Day 1 Homework — Customer support ticketing system backed by Lakebase.
Day 2 Homework — Weather intelligence: unstructured weather text ->
Lakebase vector search -> REST API.

Pages:
  GET  /                  tickets_list — all tickets, newest first
  GET  /submit            ticket_submission_form
  POST /submit            create ticket (status 'open') + first user message
  GET  /ticket/<id>       ticket_detail — ticket + messages in chronological order,
                          plus agent_reply_form and resolve button
  POST /ticket/<id>/reply agent reply (sender_type='agent')
  POST /ticket/<id>/resolve  mark ticket resolved
  POST /weather/sync      harvest weather docs from NWS API -> weather_documents
  POST /weather/embed     embed unembedded docs -> weather_embeddings
  POST /weather/search    semantic search over weather embeddings
  GET  /weather/stats     document/embedding counts
  GET  /healthz           health check
"""

import os
import threading

from flask import Flask, jsonify, redirect, render_template, request, url_for

import db
import weather_db
from weather_client import WeatherClient

app = Flask(__name__)

# The embedding model is loaded lazily (once) so app startup stays fast and
# the ~90MB download only happens when vector features are first used.
_embed_model = None
_embed_lock = threading.Lock()


def get_embed_model():
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                from sentence_transformers import SentenceTransformer

                _embed_model = SentenceTransformer(weather_db.EMBEDDING_MODEL)
    return _embed_model


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


@app.route("/")
def tickets_list():
    tickets = db.fetch_all(
        """
        SELECT id, subject, status, email, created_at
        FROM tickets
        ORDER BY created_at DESC
        """
    )
    return render_template("tickets_list.html", tickets=tickets)


@app.route("/submit", methods=["GET"])
def ticket_submission_form():
    return render_template("submit.html")


@app.route("/submit", methods=["POST"])
def submit_ticket():
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    email = request.form.get("email", "").strip()
    if not subject or not description or not email:
        return render_template(
            "submit.html", error="Subject, description, and email are all required."
        )
    ticket_id = db.insert_returning_id(
        """
        INSERT INTO tickets (subject, description, email, status)
        VALUES (%s, %s, %s, 'open')
        RETURNING id
        """,
        (subject, description, email),
    )
    # The ticket description becomes the first message in the thread.
    db.execute(
        """
        INSERT INTO ticket_messages (ticket_id, sender_type, message)
        VALUES (%s, 'user', %s)
        """,
        (ticket_id, description),
    )
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/ticket/<int:ticket_id>")
def ticket_detail(ticket_id):
    ticket = db.fetch_one("SELECT * FROM tickets WHERE id = %s", (ticket_id,))
    if ticket is None:
        return render_template("tickets_list.html", tickets=[], error="Ticket not found."), 404
    messages = db.fetch_all(
        """
        SELECT sender_type, message, created_at
        FROM ticket_messages
        WHERE ticket_id = %s
        ORDER BY created_at ASC
        """,
        (ticket_id,),
    )
    return render_template("ticket_detail.html", ticket=ticket, messages=messages)


@app.route("/ticket/<int:ticket_id>/reply", methods=["POST"])
def agent_reply(ticket_id):
    message = request.form.get("message", "").strip()
    if not message:
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))
    db.execute(
        """
        INSERT INTO ticket_messages (ticket_id, sender_type, message)
        VALUES (%s, 'agent', %s)
        """,
        (ticket_id, message),
    )
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/ticket/<int:ticket_id>/resolve", methods=["POST"])
def resolve_ticket(ticket_id):
    db.execute("UPDATE tickets SET status = 'resolved' WHERE id = %s", (ticket_id,))
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


# ---------------------------------------------------------------------------
# Day 2 — Weather intelligence endpoints
# ---------------------------------------------------------------------------


@app.route("/weather/sync", methods=["POST"])
def weather_sync():
    """
    Harvest unstructured weather text from the NWS API into weather_documents.
    Body: {"locations": ["Chicago, IL", "Austin, TX"], "limit": 50}
    """
    data = request.get_json(force=True, silent=True) or {}
    locations = data.get("locations") or []
    limit = int(data.get("limit", 50))
    if not locations:
        return jsonify({"error": "Provide 'locations', e.g. {'locations': ['Chicago, IL']}"}), 400

    weather_db.init_weather_schema()
    client = WeatherClient()
    all_docs = []
    errors = {}
    for location in locations:
        try:
            all_docs.extend(client.harvest_location(location, alert_limit=limit))
        except Exception as exc:  # noqa: BLE001 - per-location failure shouldn't kill the batch
            errors[location] = str(exc)
    synced = weather_db.upsert_documents(all_docs)
    return jsonify({"synced": synced, "locations": locations, "errors": errors})


@app.route("/weather/embed", methods=["POST"])
def weather_embed():
    """
    Chunk + embed unembedded weather documents into weather_embeddings.
    Body: {"limit": 500}
    """
    data = request.get_json(force=True, silent=True) or {}
    limit = int(data.get("limit", 500))

    weather_db.init_weather_schema()
    docs = weather_db.get_unembedded_documents(limit=limit)
    if not docs:
        return jsonify({"documents": 0, "chunks": 0, "inserted": 0})

    model = get_embed_model()
    rows = []
    for doc in docs:
        for chunk_index, chunk in enumerate(weather_db.chunk_text(doc["narrative_text"] or "")):
            emb_id = f"{doc['id']}_{chunk_index}"
            rows.append((emb_id, doc["id"], chunk_index, chunk, None, weather_db.EMBEDDING_MODEL))

    # Embed in batches, then insert.
    batch_size = 32
    final_rows = []
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        vectors = model.encode([r[3] for r in batch], show_progress_bar=False)
        for row, vec in zip(batch, vectors):
            final_rows.append((row[0], row[1], row[2], row[3], list(vec), row[5]))

    inserted = weather_db.insert_embeddings(final_rows)
    return jsonify({"documents": len(docs), "chunks": len(final_rows), "inserted": inserted})


@app.route("/weather/search", methods=["POST"])
def weather_search():
    """
    Semantic search over weather embeddings.
    Body: {"query": "flash flood risk this weekend", "top_k": 5}
    """
    data = request.get_json(force=True, silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "Provide a non-empty 'query'."}), 400
    try:
        top_k = int(data.get("top_k", 5))
    except (TypeError, ValueError):
        return jsonify({"error": "'top_k' must be an integer."}), 400
    top_k = max(1, min(20, top_k))

    model = get_embed_model()
    query_vector = model.encode([query], show_progress_bar=False)[0].tolist()
    results = weather_db.search_weather(query_vector, top_k=top_k)
    return jsonify(
        {
            "query": query,
            "top_k": top_k,
            "results": [
                {
                    "location": r["location"],
                    "headline": r["headline"],
                    "chunk_text": r["chunk_text"],
                    "similarity": round(float(r["similarity"]), 4),
                }
                for r in results
            ],
        }
    )


@app.route("/weather/stats", methods=["GET"])
def weather_stats():
    weather_db.init_weather_schema()
    return jsonify(weather_db.weather_stats())


if __name__ == "__main__":
    db.init_schema()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
