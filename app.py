"""
Day 1 Homework — Customer support ticketing system backed by Lakebase.

Pages:
  GET  /                  tickets_list — all tickets, newest first
  GET  /submit            ticket_submission_form
  POST /submit            create ticket (status 'open') + first user message
  GET  /ticket/<id>       ticket_detail — ticket + messages in chronological order,
                          plus agent_reply_form and resolve button
  POST /ticket/<id>/reply agent reply (sender_type='agent')
  POST /ticket/<id>/resolve  mark ticket resolved
  GET  /healthz           health check
"""

import os

from flask import Flask, redirect, render_template, request, url_for

import db

app = Flask(__name__)


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


if __name__ == "__main__":
    db.init_schema()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=True)
