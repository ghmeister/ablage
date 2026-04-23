"""
OpenAI API cost tracking.

Logs token usage to the same SQLite database as the document archive.

Usage in bot.py (one document = one session):
    session_id = cost_tracker.begin_session()
    ... process document (OCR, classify, embed) ...
    cost_tracker.tag_session(session_id, new_doc_id)
    cost_tracker.clear_session()

All log() calls on the same thread automatically pick up the active session.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(os.getenv("DB_PATH", "/data/documents.db"))

# USD per 1 000 tokens  (input, output)
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":                 (0.0025,  0.010),
    "gpt-4o-mini":            (0.00015, 0.0006),
    "text-embedding-3-small": (0.00002, 0.0),
}

_session = threading.local()


# ── internal helpers ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_costs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT    NOT NULL,
            model         TEXT    NOT NULL,
            purpose       TEXT    NOT NULL,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd      REAL    NOT NULL DEFAULT 0.0,
            session_id    TEXT,
            document_id   INTEGER
        )
    """)
    # Migrate older tables that are missing the two new columns
    for col, typedef in (("session_id", "TEXT"), ("document_id", "INTEGER")):
        try:
            conn.execute(f"ALTER TABLE api_costs ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_costs_ts  ON api_costs(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_costs_sid ON api_costs(session_id)")
    conn.commit()


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _PRICING.get(model, _PRICING["gpt-4o-mini"])
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1000.0


# ── session management (used by bot.py) ──────────────────────────────────────

def begin_session() -> str:
    """Start a new processing session; returns a session_id."""
    sid = str(uuid.uuid4())
    _session.current = sid
    return sid


def clear_session() -> None:
    _session.current = None


def tag_session(session_id: str, document_id: int) -> None:
    """Link all api_costs rows for this session to the given document."""
    try:
        conn = _connect()
        _ensure_table(conn)
        conn.execute(
            "UPDATE api_costs SET document_id = ? WHERE session_id = ?",
            (document_id, session_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── logging ───────────────────────────────────────────────────────────────────

def log(model: str, purpose: str, usage: Any) -> None:
    """Record an API call. usage is the OpenAI response.usage object."""
    try:
        if hasattr(usage, "prompt_tokens"):
            input_tokens  = usage.prompt_tokens or 0
            output_tokens = usage.completion_tokens or 0
        elif hasattr(usage, "total_tokens"):
            input_tokens  = usage.total_tokens or 0
            output_tokens = 0
        else:
            return

        cost = _compute_cost(model, input_tokens, output_tokens)
        ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        sid  = getattr(_session, "current", None)

        conn = _connect()
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO api_costs"
            " (timestamp, model, purpose, input_tokens, output_tokens, cost_usd, session_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, model, purpose, input_tokens, output_tokens, cost, sid),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # cost tracking is non-critical


# ── query helpers ─────────────────────────────────────────────────────────────

def get_summary() -> dict:
    """Today's cost, total cost, breakdown by model and purpose."""
    try:
        conn = _connect()
        _ensure_table(conn)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        today_usd = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM api_costs WHERE timestamp LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]

        total_usd = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) FROM api_costs",
        ).fetchone()[0]

        by_model = [
            {"model": r["model"], "total_usd": r["total_usd"]}
            for r in conn.execute(
                "SELECT model, SUM(cost_usd) AS total_usd FROM api_costs"
                " GROUP BY model ORDER BY total_usd DESC"
            ).fetchall()
        ]

        by_purpose = [
            {"purpose": r["purpose"], "total_usd": r["total_usd"]}
            for r in conn.execute(
                "SELECT purpose, SUM(cost_usd) AS total_usd FROM api_costs"
                " GROUP BY purpose ORDER BY total_usd DESC"
            ).fetchall()
        ]

        days_tracked = conn.execute(
            "SELECT COUNT(DISTINCT substr(timestamp,1,10)) FROM api_costs"
        ).fetchone()[0]

        conn.close()
        return {
            "today_usd":    round(today_usd, 6),
            "total_usd":    round(total_usd, 6),
            "by_model":     by_model,
            "by_purpose":   by_purpose,
            "days_tracked": days_tracked,
        }
    except Exception:
        return {"today_usd": 0.0, "total_usd": 0.0,
                "by_model": [], "by_purpose": [], "days_tracked": 0}


def get_daily_totals(days: int = 30) -> list[dict]:
    """Per-day totals for the last N days, newest first."""
    try:
        conn = _connect()
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT substr(timestamp,1,10) AS day,"
            "       SUM(cost_usd) AS total_usd,"
            "       COUNT(DISTINCT session_id) AS doc_count,"
            "       SUM(input_tokens + output_tokens) AS total_tokens"
            " FROM api_costs"
            " GROUP BY day"
            " ORDER BY day DESC"
            " LIMIT ?",
            (days,),
        ).fetchall()
        conn.close()
        return [
            {
                "day":          r["day"],
                "total_usd":    round(r["total_usd"], 6),
                "doc_count":    r["doc_count"],
                "total_tokens": r["total_tokens"],
            }
            for r in rows
        ]
    except Exception:
        return []


def get_monthly_totals() -> list[dict]:
    """Per-month totals, newest first."""
    try:
        conn = _connect()
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT substr(timestamp,1,7) AS month,"
            "       SUM(cost_usd) AS total_usd,"
            "       COUNT(DISTINCT session_id) AS doc_count,"
            "       SUM(input_tokens + output_tokens) AS total_tokens"
            " FROM api_costs"
            " GROUP BY month"
            " ORDER BY month DESC",
        ).fetchall()
        conn.close()
        return [
            {
                "month":        r["month"],
                "total_usd":    round(r["total_usd"], 6),
                "doc_count":    r["doc_count"],
                "total_tokens": r["total_tokens"],
            }
            for r in rows
        ]
    except Exception:
        return []


def get_per_document_costs(limit: int = 50) -> list[dict]:
    """Cost per document (grouped by session), most expensive first."""
    try:
        conn = _connect()
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT ac.session_id,"
            "       ac.document_id,"
            "       d.new_filename,"
            "       d.document_type,"
            "       d.scan_timestamp,"
            "       SUM(ac.cost_usd) AS total_usd,"
            "       SUM(ac.input_tokens + ac.output_tokens) AS total_tokens,"
            "       GROUP_CONCAT(DISTINCT ac.purpose) AS purposes"
            " FROM api_costs ac"
            " LEFT JOIN documents d ON d.id = ac.document_id"
            " WHERE ac.session_id IS NOT NULL"
            " GROUP BY ac.session_id"
            " ORDER BY total_usd DESC"
            " LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [
            {
                "session_id":   r["session_id"],
                "document_id":  r["document_id"],
                "filename":     r["new_filename"],
                "document_type": r["document_type"],
                "scan_timestamp": r["scan_timestamp"],
                "total_usd":    round(r["total_usd"], 6),
                "total_tokens": r["total_tokens"],
                "purposes":     r["purposes"] or "",
            }
            for r in rows
        ]
    except Exception:
        return []
