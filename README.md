# Ticketing App — Day 1 Homework

Customer support ticketing system backed by Lakebase (Databricks-managed Postgres),
built for the DataExpert.io "The Rise of the AI Data Engineer" bootcamp.

## What it does

- **Ticket submission form** (`/submit`) — users submit tickets with subject, description, email
- **Tickets list** (`/`) — all tickets, newest first (Subject, Status, Submitted By, Created At)
- **Ticket detail** (`/ticket/<id>`) — full ticket plus all messages in chronological order
- **Agent reply form** — support agents reply to tickets
- **Mark resolved** — flips ticket status from `open` to `resolved`

## Database

Two tables in Lakebase Postgres (created automatically on app startup):

- `tickets` — id, subject, description, email, status (default `'open'`), created_at
- `ticket_messages` — id, ticket_id → tickets(id), sender_type (`'user'`/`'agent'`), message, created_at

## Local dev

```bash
cp .env.example .env   # fill in LAKEBASE_URL
pip install -r requirements.txt
python app.py
```

## Deploy (Databricks Apps)

1. In your Databricks workspace: **Workspace → Create → Git folder**, add this repo.
2. **Compute → Apps → Create app (Custom)**, point it at the Git folder.
3. Add environment variable `LAKEBASE_URL` with your Lakebase connection string:
   `postgresql://<role>:<password>@<host>:5432/databricks_postgres?sslmode=require`
4. Deploy. Tables are created automatically on first startup.
