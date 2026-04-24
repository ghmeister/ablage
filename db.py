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
_vec_available: bool = False  # set to True once sqlite-vec loads successfully


def _try_load_vec(conn: sqlite3.Connection) -> bool:
    global _vec_available
    try:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        _vec_available = True
        return True
    except Exception:
        return False


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
    matched_rule       TEXT,
    tax_relevant       INTEGER NOT NULL DEFAULT 0,
    seen               INTEGER NOT NULL DEFAULT 0,
    content_hash       TEXT,
    email_source       INTEGER NOT NULL DEFAULT 0,
    email_from         TEXT,
    email_subject      TEXT,
    email_date         TEXT,
    email_message_id   TEXT
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
    _try_load_vec(conn)
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
        if _vec_available:
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents "
                    "USING vec0(embedding float[1536])"
                )
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
                    "USING vec0(embedding float[1536])"
                )
                conn.commit()
            except Exception:
                pass
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chunks (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                chunk_text  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
        """)
        conn.commit()
        # Migration: add tax_relevant column if it doesn't exist yet
        for migration in [
            "ALTER TABLE documents ADD COLUMN tax_relevant INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE documents ADD COLUMN email_source INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE documents ADD COLUMN email_from TEXT",
            "ALTER TABLE documents ADD COLUMN email_subject TEXT",
            "ALTER TABLE documents ADD COLUMN email_date TEXT",
            "ALTER TABLE documents ADD COLUMN email_message_id TEXT",
            "ALTER TABLE documents ADD COLUMN seen INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE documents ADD COLUMN content_hash TEXT",
        ]:
            try:
                conn.execute(migration)
                conn.commit()
                # Mark all pre-existing documents as seen so they don't appear as new
                if "seen" in migration:
                    conn.execute("UPDATE documents SET seen = 1")
                    conn.commit()
            except Exception:
                pass  # Column already exists
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
    tax_relevant: int = 0,
    email_source: int = 0,
    email_from: Optional[str] = None,
    email_subject: Optional[str] = None,
    email_date: Optional[str] = None,
    email_message_id: Optional[str] = None,
    content_hash: Optional[str] = None,
) -> int:
    """Insert a document record and return the new row id."""
    ts = scan_timestamp or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO documents
                (scan_timestamp, original_filename, new_filename, destination_folder,
                 onedrive_path, document_type, document_date, sender, recipient,
                 company, keywords, extracted_text, matched_rule, tax_relevant,
                 email_source, email_from, email_subject, email_date, email_message_id,
                 content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, original_filename, new_filename, destination_folder,
             onedrive_path, document_type, document_date, sender, recipient,
             company, keywords, extracted_text, matched_rule, tax_relevant,
             email_source, email_from, email_subject, email_date, email_message_id,
             content_hash),
        )
        return cur.lastrowid


def find_duplicate_by_hash(content_hash: str, exclude_id: Optional[int] = None) -> Optional[int]:
    """Return the id of an existing document with the same content hash, or None."""
    with _conn() as conn:
        if exclude_id:
            row = conn.execute(
                "SELECT id FROM documents WHERE content_hash = ? AND id != ? LIMIT 1",
                (content_hash, exclude_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM documents WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
        return row[0] if row else None


def get_unseen_count() -> int:
    """Return the number of documents with seen=0."""
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM documents WHERE seen = 0").fetchone()[0]


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


def update_document(doc_id: int, **fields) -> None:
    """Update specific fields of a document record."""
    _allowed = {
        "new_filename", "document_type", "destination_folder",
        "onedrive_path", "matched_rule",
        "document_date", "sender", "recipient", "company",
        "tax_relevant", "seen", "content_hash",
    }
    updates = {k: v for k, v in fields.items() if k in _allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with _conn() as conn:
        conn.execute(
            f"UPDATE documents SET {set_clause} WHERE id = ?",
            list(updates.values()) + [doc_id],
        )


def delete_document(doc_id: int) -> None:
    """Delete a document record by id."""
    with _conn() as conn:
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        try:
            conn.execute("DELETE FROM vec_documents WHERE rowid = ?", (doc_id,))
        except Exception:
            pass


def store_embedding(doc_id: int, vector: list[float]) -> None:
    """Store a float32 embedding vector for a document."""
    if not _vec_available:
        return
    from embed import serialize
    blob = serialize(vector)
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO vec_documents(rowid, embedding) VALUES (?, vec_f32(?))",
            (doc_id, blob),
        )


def search_by_embedding(
    vector: list[float], k: int = 20, max_distance: float = 0.6
) -> list[tuple[int, float]]:
    """Return (doc_id, distance) pairs for the k nearest neighbours below max_distance."""
    if not _vec_available:
        return []
    from embed import serialize
    blob = serialize(vector)
    with _conn() as conn:
        try:
            rows = conn.execute(
                "SELECT rowid, distance FROM vec_documents "
                "WHERE embedding MATCH vec_f32(?) ORDER BY distance LIMIT ?",
                (blob, k),
            ).fetchall()
            return [(r[0], r[1]) for r in rows if r[1] <= max_distance]
        except Exception:
            return []


def get_documents_without_embedding() -> list[dict]:
    """Return all documents that have no embedding stored yet."""
    with _conn() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM documents "
                "WHERE id NOT IN (SELECT rowid FROM vec_documents) "
                "ORDER BY id"
            ).fetchall()
        except Exception:
            rows = conn.execute("SELECT * FROM documents ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def store_chunks(doc_id: int, chunks: list[str], vectors: list[list[float]]) -> None:
    """Replace all chunks for a document and store their embeddings."""
    if not _vec_available:
        return
    from embed import serialize
    with _conn() as conn:
        # Remove old chunks + embeddings for this doc
        old_ids = [r[0] for r in conn.execute(
            "SELECT id FROM chunks WHERE doc_id = ?", (doc_id,)
        ).fetchall()]
        for cid in old_ids:
            try:
                conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (cid,))
            except Exception:
                pass
        conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

        for idx, (text, vector) in enumerate(zip(chunks, vectors)):
            cur = conn.execute(
                "INSERT INTO chunks(doc_id, chunk_index, chunk_text) VALUES (?, ?, ?)",
                (doc_id, idx, text),
            )
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, vec_f32(?))",
                (chunk_id, serialize(vector)),
            )


def search_by_chunk_embedding(
    vector: list[float], k: int = 10, max_distance: float = 1.05
) -> list[tuple[int, int, float, str]]:
    """Return (doc_id, chunk_id, distance, chunk_text) for nearest chunk matches."""
    if not _vec_available:
        return []
    from embed import serialize
    blob = serialize(vector)
    with _conn() as conn:
        try:
            rows = conn.execute(
                "SELECT c.doc_id, vc.rowid, vc.distance, c.chunk_text "
                "FROM vec_chunks vc "
                "JOIN chunks c ON c.id = vc.rowid "
                "WHERE vc.embedding MATCH vec_f32(?) ORDER BY vc.distance LIMIT ?",
                (blob, k * 3),  # fetch extra; we deduplicate by doc below
            ).fetchall()
            seen_docs: set[int] = set()
            results = []
            for doc_id, chunk_id, dist, text in rows:
                if dist > max_distance:
                    continue
                if doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    results.append((doc_id, chunk_id, dist, text))
                if len(results) >= k:
                    break
            return results
        except Exception:
            return []


def get_best_chunk_for_doc(doc_id: int, query_vector: list[float], max_chars: int = 1200) -> str:
    """Return all chunk text for a doc concatenated (up to max_chars).

    Concatenating is more reliable than per-chunk vector ranking and ensures
    price/amount lines aren't missed because they happened to be in chunk 2.
    """
    with _conn() as conn:
        try:
            rows = conn.execute(
                "SELECT chunk_text FROM chunks WHERE doc_id = ? ORDER BY chunk_index",
                (doc_id,),
            ).fetchall()
            parts = []
            total = 0
            for (text,) in rows:
                if total >= max_chars:
                    break
                snippet = text[:max_chars - total]
                parts.append(snippet)
                total += len(snippet)
            return " … ".join(parts)
        except Exception:
            return ""


def get_docs_without_chunks() -> list[dict]:
    """Return documents that have no chunks stored yet but have extracted_text."""
    with _conn() as conn:
        try:
            rows = conn.execute(
                "SELECT * FROM documents "
                "WHERE extracted_text IS NOT NULL AND extracted_text != '' "
                "AND id NOT IN (SELECT DISTINCT doc_id FROM chunks) "
                "ORDER BY id"
            ).fetchall()
        except Exception:
            rows = []
        return [dict(r) for r in rows]


_SORTABLE_COLUMNS = {
    "document_date", "new_filename", "document_type",
    "sender", "recipient", "destination_folder", "scan_timestamp",
}


def search_documents(
    query: Optional[str] = None,
    document_type: Optional[str] = None,
    year: Optional[str] = None,
    year_from: Optional[str] = None,
    sender: Optional[str] = None,
    recipient: Optional[str] = None,
    email_only: bool = False,
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

    if year_from:
        conds.append("substr(document_date, 1, 4) >= ?")
        params.append(year_from)

    if sender:
        conds.append("(sender LIKE ? OR company LIKE ? OR email_from LIKE ?)")
        params.extend([f"%{sender}%", f"%{sender}%", f"%{sender}%"])

    if recipient:
        conds.append("recipient LIKE ?")
        params.append(f"%{recipient}%")

    if email_only:
        conds.append("email_source = 1")

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


def get_tax_relevant_documents(year: Optional[str] = None) -> tuple[list[dict], list[str]]:
    """
    Return (docs, years) where docs are all tax_relevant=1 records,
    optionally filtered by year, sorted by document_type then document_date.
    years is the list of distinct years with tax-relevant documents (newest first).
    """
    with _conn() as conn:
        year_rows = conn.execute(
            "SELECT DISTINCT substr(document_date, 1, 4) AS yr "
            "FROM documents WHERE tax_relevant = 1 AND document_date IS NOT NULL "
            "ORDER BY yr DESC"
        ).fetchall()
        years = [r[0] for r in year_rows if r[0]]

        params: list = []
        conds = ["tax_relevant = 1"]
        if year:
            conds.append("substr(document_date, 1, 4) = ?")
            params.append(year)
        where = "WHERE " + " AND ".join(conds)
        rows = conn.execute(
            f"SELECT * FROM documents {where} "
            "ORDER BY document_type ASC, document_date DESC",
            params,
        ).fetchall()

    return [dict(r) for r in rows], years


def get_duplicate_groups() -> list[list[dict]]:
    """Return groups of documents sharing the same content_hash (2+ per group)."""
    with _conn() as conn:
        hashes = conn.execute(
            "SELECT content_hash FROM documents WHERE content_hash IS NOT NULL "
            "GROUP BY content_hash HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC"
        ).fetchall()
        groups = []
        for (h,) in hashes:
            rows = conn.execute(
                "SELECT * FROM documents WHERE content_hash = ? ORDER BY id",
                (h,),
            ).fetchall()
            groups.append([dict(r) for r in rows])
    return groups


def get_statistics() -> dict:
    """Return aggregate statistics about the document archive."""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        this_month = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE substr(scan_timestamp,1,7) = strftime('%Y-%m','now')"
        ).fetchone()[0]
        last_month = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE substr(scan_timestamp,1,7) = strftime('%Y-%m','now','-1 month')"
        ).fetchone()[0]
        tax_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE tax_relevant = 1"
        ).fetchone()[0]
        email_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE email_source = 1"
        ).fetchone()[0]
        by_type = conn.execute(
            "SELECT document_type, COUNT(*) AS cnt FROM documents "
            "WHERE document_type IS NOT NULL "
            "GROUP BY document_type ORDER BY cnt DESC LIMIT 8"
        ).fetchall()
        top_senders = conn.execute(
            "SELECT COALESCE(NULLIF(sender,''), NULLIF(company,''), '(unbekannt)') AS name,"
            "       COUNT(*) AS cnt"
            " FROM documents"
            " GROUP BY name ORDER BY cnt DESC LIMIT 15"
        ).fetchall()
    return {
        "total": total,
        "this_month": this_month,
        "last_month": last_month,
        "tax_relevant": tax_count,
        "email_source": email_count,
        "by_type": [(r[0], r[1]) for r in by_type],
        "top_senders": [{"name": r[0], "count": r[1]} for r in top_senders],
    }


def get_archive_stats() -> dict:
    """Comprehensive archive statistics for the /statistik page."""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        oldest = conn.execute(
            "SELECT MIN(document_date) FROM documents WHERE document_date IS NOT NULL"
        ).fetchone()[0]
        newest = conn.execute(
            "SELECT MAX(document_date) FROM documents WHERE document_date IS NOT NULL"
        ).fetchone()[0]

        this_month = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE substr(scan_timestamp,1,7) = strftime('%Y-%m','now')"
        ).fetchone()[0]
        last_month = conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE substr(scan_timestamp,1,7) = strftime('%Y-%m','now','-1 month')"
        ).fetchone()[0]

        unseen = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE seen = 0"
        ).fetchone()[0]

        tax_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE tax_relevant = 1"
        ).fetchone()[0]

        email_count = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE email_source = 1"
        ).fetchone()[0]

        # Duplicates
        dup_groups = conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT content_hash FROM documents WHERE content_hash IS NOT NULL"
            "  GROUP BY content_hash HAVING COUNT(*) > 1"
            ")"
        ).fetchone()[0]
        dup_docs = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE content_hash IN ("
            "  SELECT content_hash FROM documents WHERE content_hash IS NOT NULL"
            "  GROUP BY content_hash HAVING COUNT(*) > 1"
            ")"
        ).fetchone()[0]

        # By type (all)
        by_type = [
            {"type": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT document_type, COUNT(*) AS cnt FROM documents"
                " WHERE document_type IS NOT NULL"
                " GROUP BY document_type ORDER BY cnt DESC"
            ).fetchall()
        ]

        # By year (from document_date)
        by_year = [
            {"year": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT substr(document_date,1,4) AS yr, COUNT(*) AS cnt"
                " FROM documents WHERE document_date IS NOT NULL AND yr != ''"
                " GROUP BY yr ORDER BY yr ASC"
            ).fetchall()
        ]

        # Documents added per month (scan_timestamp, last 24 months)
        by_scan_month = [
            {"month": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT substr(scan_timestamp,1,7) AS mo, COUNT(*) AS cnt"
                " FROM documents"
                " WHERE mo >= strftime('%Y-%m','now','-23 months')"
                " GROUP BY mo ORDER BY mo ASC"
            ).fetchall()
        ]

        # Top senders (company preferred, fall back to sender)
        top_senders = [
            {"name": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT COALESCE(NULLIF(company,''), NULLIF(sender,''), '(unbekannt)') AS name,"
                "       COUNT(*) AS cnt"
                " FROM documents"
                " GROUP BY name ORDER BY cnt DESC LIMIT 10"
            ).fetchall()
        ]

        # OCR usage: docs whose session_id has an 'ocr' purpose in api_costs
        ocr_count = 0
        try:
            ocr_count = conn.execute(
                "SELECT COUNT(DISTINCT document_id) FROM api_costs"
                " WHERE purpose = 'ocr' AND document_id IS NOT NULL"
            ).fetchone()[0]
        except Exception:
            pass

    return {
        "total":         total,
        "oldest":        oldest,
        "newest":        newest,
        "this_month":    this_month,
        "last_month":    last_month,
        "unseen":        unseen,
        "tax_relevant":  tax_count,
        "email_source":  email_count,
        "dup_groups":    dup_groups,
        "dup_docs":      dup_docs,
        "ocr_count":     ocr_count,
        "by_type":       by_type,
        "by_year":       by_year,
        "by_scan_month": by_scan_month,
        "top_senders":   top_senders,
    }


def _sanitize_fts_query(query: str) -> str:
    """Convert a user search string into a safe FTS5 MATCH expression."""
    for ch in ('"', "'", '(', ')', '-', '+', '^', '*', ':', '.', ','):
        query = query.replace(ch, ' ')
    tokens = [t for t in query.split() if t]
    if not tokens:
        return '""'
    return ' '.join(f'"{t}"*' for t in tokens)
