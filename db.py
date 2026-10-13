"""
Database helper for the ticketing app.

Connects to Lakebase (Databricks-managed Postgres) using a single
LAKEBASE_URL connection string, e.g.:
    postgresql://<role>:<password>@<host>:5432/databricks_postgres?sslmode=require

Resolution order:
  1. LAKEBASE_URL environment variable (local dev via .env, or injected).
  2. Databricks secret scope (deployed on Databricks Apps), using
     LAKEBASE_SECRET_SCOPE / LAKEBASE_SECRET_KEY (defaults: database / lakebase-url).
"""

import base64
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _lakebase_url() -> str:
    url = os.environ.get("LAKEBASE_URL")
    if url:
        return url
    # Fall back to the Databricks secret scope (deployed app path).
    from databricks.sdk import WorkspaceClient

    scope = os.environ.get("LAKEBASE_SECRET_SCOPE", "database")
    key = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")
    secret = WorkspaceClient().secrets.get_secret(scope=scope, key=key)
    return base64.b64decode(secret.value).decode("utf-8")


@contextmanager
def get_connection():
    """Yield a psycopg2 connection with RealDictCursor rows."""
    conn = psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params=None) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def fetch_one(sql: str, params=None):
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params=None) -> int:
    """Run INSERT/UPDATE/DELETE; return affected row count."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


def insert_returning_id(sql: str, params=None):
    """Run an INSERT ... RETURNING id and return the new id."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            new_id = cur.fetchone()["id"]
            conn.commit()
            return new_id


def init_schema() -> None:
    """Create the tickets and ticket_messages tables if they don't exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id SERIAL PRIMARY KEY,
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    email TEXT NOT NULL,
                    status TEXT DEFAULT 'open',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_messages (
                    id SERIAL PRIMARY KEY,
                    ticket_id INTEGER REFERENCES tickets(id),
                    sender_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()
