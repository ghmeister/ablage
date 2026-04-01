"""
SQLite document index with FTS5 full-text search for the PDF Renamer archive.

DB_PATH env var controls the file path (default: /data/documents.db).
When running locally, set DB_PATH to a local path, e.g.:
    DB_PATH=./documents.db python index_existing.py ...
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

_db_path_override: Optional[Path] = None


def _get_db_path() -> Path:
    if _db_path_override is not None:
        return _db_path_override
    p = Path(os.getenv("DB_PATH", "/data/documents.db"))
    if not p.is_absolute():
        p = Path(__file__).parent / p
    return p


def set_db_path(path: str | Path) -> None:
    """Override DB path at runtime (for CLI tools and tests)."""
    global _db_path_override
    _db_path_override = Path(path)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_timestamp     TEXT    NOT NULL,
    original_filename  TEXT,
    new_filename       TEXT    NOT NULL,
    destination_folder TEXT,
    onedrive_path      TEXT,
    document_type      TEXT,
    document_date      TEXT,
    sender             TEXT,
    recipient          TEXT,
    company            TEXT,
    keywords           TEXT,
    extracted_text     TEXT,
    matched_rule       TEXT
);

CREATE INDEX IF NOT EXISTS idx_doc_type  ON documents(document_type);
CREATE INDEX IF NOT EXISTS idx_doc_date  ON documents(document_date);
CREATE INDEX IF NOT EXISTS idx_doc_fname ON documents(new_filename);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    new_filename,
    sender,
    recipient,
    company,
    keywords,
    extracted_text,
    content=documents,
    content_rowid=id
);

CREATE TRIGGER IF NOT EXISTS docs_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, new_filename, sender, recipient, company, keywords, extracted_text)
    VALUES (new.id, new.new_filename, new.sender, new.recipient, new.company, new.keywords, new.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS docs_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, new_filename, sender, recipient, company, keywords, extracted_text)
    VALUES ('delete', old.id, old.new_filename, old.sender, old.recipient, old.company, old.keywords, old.extracted_text);
END;

CREATE TRIGGER IF NOT EXISTS docs_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, new_filename, sender, recipient, company, keywords, extracted_text)
    VALUES ('delete', old.id, old.new_filename, old.sender, old.recipient, old.company, old.keywords, old.extracted_text);
    INSERT INTO documents_fts(rowid, new_filename, sender, recipient, company, keywords, extracted_text)
    VALUES (new.id, new.new_filename, new.sender, new.recipient, new.company, new.keywords, new.extracted_text);
END;
"""


def _get_connection() -> sqlite3.Connection:
    path = _get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = _get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables, indexes, FTS virtual table, and triggers if not present."""
    conn = _get_connection()
    try:
        conn.executescript(_SCHEMA)
    finally:
        conn.close()


def insert_document(
    *,
    original_filename: Optional[str],
    new_filename: str,
    destination_folder: Optional[str] = None,
    onedrive_path: Optional[str] = None,
    document_type: Optional[str] = None,
    document_date: Optional[str] = None,
    sender: Optional[str] = None,
    recipient: Optional[str] = None,
    company: Optional[str] = None,
    keywords: Optional[str] = None,
    extracted_text: Optional[str] = None,
    matched_rule: Optional[str] = None,
    scan_timestamp: Optional[str] = None,
) -> int:
    """Insert a document record and return the new row id."""
    ts = scan_timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO documents
                (scan_timestamp, original_filename, new_filename, destination_folder,
                 onedrive_path, document_type, document_date, sender, recipient,
                 company, keywords, extracted_text, matched_rule)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, original_filename, new_filename, destination_folder,
             onedrive_path, document_type, document_date, sender, recipient,
             company, keywords, extracted_text, matched_rule),
        )
        return cur.lastrowid


def document_exists(new_filename: str) -> bool:
    """Return True if a document with this new_filename is already in the index."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM documents WHERE new_filename = ? LIMIT 1",
            (new_filename,),
        ).fetchone()
        return row is not None


def get_document(doc_id: int) -> Optional[dict]:
    """Fetch a single document by id."""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        return dict(row) if row else None


_SORTABLE_COLUMNS = {
    "document_date", "new_filename", "document_type",
    "sender", "recipient", "destination_folder", "scan_timestamp",
}


def search_documents(
    query: Optional[str] = None,
    document_type: Optional[str] = None,
    year: Optional[str] = None,
    sender: Optional[str] = None,
    page: int = 1,
    per_page: int = 25,
    sort_by: str = "scan_timestamp",
    sort_order: str = "desc",
) -> tuple[list[dict], int]:
    """
    Search/filter documents. Returns (rows, total_count).
    All filters are AND-combined.
    """
    if sort_by not in _SORTABLE_COLUMNS:
        sort_by = "scan_timestamp"
    order_sql = "DESC" if sort_order.lower() == "desc" else "ASC"
    conds: list[str] = []
    params: list = []

    fts_query = _sanitize_fts_query(query) if (query and query.strip()) else None

    if fts_query:
        conds.append("id IN (SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?)")
        params.append(fts_query)

    if document_type:
        conds.append("document_type = ?")
        params.append(document_type)

    if year:
        conds.append("substr(document_date, 1, 4) = ?")
        params.append(year)

    if sender:
        conds.append("(sender LIKE ? OR company LIKE ?)")
        params.extend([f"%{sender}%", f"%{sender}%"])

    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    with _conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM documents {where}", params
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT * FROM documents {where} ORDER BY {sort_by} {order_sql} LIMIT ? OFFSET ?",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

    return [dict(r) for r in rows], total


def get_distinct_values(column: str) -> list[str]:
    """Return sorted distinct non-null values for a column (for filter dropdowns)."""
    _allowed = {"document_type", "sender", "recipient", "company", "destination_folder"}
    if column not in _allowed:
        raise ValueError(f"Column not allowed: {column}")
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT DISTINCT {column} FROM documents "
            f"WHERE {column} IS NOT NULL AND {column} != '' ORDER BY {column}"
        ).fetchall()
    return [r[0] for r in rows]


def get_distinct_years() -> list[str]:
    """Return sorted distinct years from document_date (newest first)."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT substr(document_date, 1, 4) AS yr "
            "FROM documents WHERE document_date IS NOT NULL "
            "ORDER BY yr DESC"
        ).fetchall()
    return [r[0] for r in rows if r[0]]


def _sanitize_fts_query(query: str) -> str:
    """Convert a user search string into a safe FTS5 MATCH expression."""
    for ch in ('"', "'", '(', ')', '-', '+', '^', '*', ':', '.', ','):
        query = query.replace(ch, ' ')
    tokens = [t for t in query.split() if t]
    if not tokens:
        return '""'
    return ' '.join(f'"{t}"*' for t in tokens)
