"""
OpenAI API cost tracking.

Logs token usage to the same SQLite database as the document archive.
Call log() after each OpenAI API call; get_summary() returns today's and
total accumulated costs.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DB_PATH = Path(os.getenv("DB_PATH", "/data/documents.db"))

# USD per 1 000 tokens (input / output)
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o":                   (0.0025,  0.010),
    "gpt-4o-mini":              (0.00015, 0.0006),
    "text-embedding-3-small":   (0.00002, 0.0),    # embeddings have no output tokens
}


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
            cost_usd      REAL    NOT NULL DEFAULT 0.0
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_api_costs_ts ON api_costs(timestamp)")
    conn.commit()


def _compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    prices = _PRICING.get(model)
    if not prices:
        # Unknown model — estimate at gpt-4o-mini rate to avoid silent zero
        prices = _PRICING["gpt-4o-mini"]
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / 1000.0


def log(model: str, purpose: str, usage: Any) -> None:
    """
    Record an API call.

    usage — the usage object from an OpenAI response
            (has .prompt_tokens / .completion_tokens, or .total_tokens for embeddings)
    """
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

        conn = _connect()
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO api_costs (timestamp, model, purpose, input_tokens, output_tokens, cost_usd)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (ts, model, purpose, input_tokens, output_tokens, cost),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # cost tracking is non-critical


def get_summary() -> dict:
    """
    Return cost totals.

    Keys:
        today_usd   – cost accumulated today (UTC)
        total_usd   – all-time cost
        by_model    – list of {model, total_usd} sorted descending
        by_purpose  – list of {purpose, total_usd} sorted descending
        days_tracked – number of distinct days with recorded costs
    """
    try:
        conn = _connect()
        _ensure_table(conn)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        today_usd = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_costs WHERE timestamp LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]

        total_usd = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_costs",
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
            "SELECT COUNT(DISTINCT substr(timestamp, 1, 10)) FROM api_costs"
        ).fetchone()[0]

        conn.close()
        return {
            "today_usd":    round(today_usd, 4),
            "total_usd":    round(total_usd, 4),
            "by_model":     by_model,
            "by_purpose":   by_purpose,
            "days_tracked": days_tracked,
        }
    except Exception:
        return {
            "today_usd":    0.0,
            "total_usd":    0.0,
            "by_model":     [],
            "by_purpose":   [],
            "days_tracked": 0,
        }
