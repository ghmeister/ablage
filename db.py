"""
SQLite document index with FTS5 full-text search for the PDF Renamer archive.

DB_PATH env var controls the file path (default: /data/documents.db).
When running locally, set DB_PATH to a local path, e.g.:
    DB_PATH=./documents.db python index_existing.py ...
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

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

            -- KI-Suche chat history
            CREATE TABLE IF NOT EXISTS ki_chats (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                title      TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ki_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL REFERENCES ki_chats(id) ON DELETE CASCADE,
                role       TEXT NOT NULL,            -- 'user' or 'assistant'
                content    TEXT NOT NULL,
                documents  TEXT,                     -- JSON list of linked documents (assistant only)
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ki_messages_chat ON ki_messages(chat_id, id);

            -- Settings edited in the web UI (Einstellungen); lists are JSON arrays
            CREATE TABLE IF NOT EXISTS persons (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,     -- first name as used in filenames
                variants   TEXT NOT NULL DEFAULT '[]',
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sender_groups (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL UNIQUE,  -- canonical short name, e.g. KPT
                variants      TEXT NOT NULL DEFAULT '[]',
                search_terms  TEXT NOT NULL DEFAULT '[]',
                document_type TEXT,
                note          TEXT
            );
            CREATE TABLE IF NOT EXISTS app_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        conn.commit()
        _seed_settings_from_yaml(conn)
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
            # Sender as found on the document, kept when sender is set to a group's short name
            "ALTER TABLE documents ADD COLUMN sender_original TEXT",
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


# ── KI-Suche chat history ─────────────────────────────────────────────────────

def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ki_list_chats(limit: int = 100) -> list[dict]:
    """Chats, most recently used first."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, title, created_at, updated_at FROM ki_chats ORDER BY updated_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def ki_get_chat(chat_id: int) -> Optional[dict]:
    """A chat with its messages in order, or None."""
    import json as _json
    with _conn() as conn:
        chat = conn.execute("SELECT id, title, created_at, updated_at FROM ki_chats WHERE id = ?", (chat_id,)).fetchone()
        if chat is None:
            return None
        msgs = conn.execute(
            "SELECT role, content, documents, created_at FROM ki_messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    out = dict(chat)
    out["messages"] = [
        {"role": m["role"], "content": m["content"], "created_at": m["created_at"],
         "documents": _json.loads(m["documents"]) if m["documents"] else []}
        for m in msgs
    ]
    return out


def ki_save_exchange(chat_id: Optional[int], question: str, answer: str, documents: list[dict]) -> int:
    """Store one question + answer; creates the chat when chat_id is None. Returns the chat id."""
    import json as _json
    now = _now()
    with _conn() as conn:
        if chat_id is None or conn.execute("SELECT 1 FROM ki_chats WHERE id = ?", (chat_id,)).fetchone() is None:
            title = " ".join(question.split())
            title = title if len(title) <= 80 else title[:77].rstrip() + "…"
            chat_id = conn.execute(
                "INSERT INTO ki_chats(title, created_at, updated_at) VALUES (?, ?, ?)", (title, now, now)
            ).lastrowid
        else:
            conn.execute("UPDATE ki_chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        conn.execute(
            "INSERT INTO ki_messages(chat_id, role, content, documents, created_at) VALUES (?, 'user', ?, NULL, ?)",
            (chat_id, question, now),
        )
        conn.execute(
            "INSERT INTO ki_messages(chat_id, role, content, documents, created_at) VALUES (?, 'assistant', ?, ?, ?)",
            (chat_id, answer, _json.dumps(documents, ensure_ascii=False), now),
        )
    return chat_id


def ki_delete_chat(chat_id: int) -> bool:
    with _conn() as conn:
        conn.execute("DELETE FROM ki_messages WHERE chat_id = ?", (chat_id,))
        return conn.execute("DELETE FROM ki_chats WHERE id = ?", (chat_id,)).rowcount > 0


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
    sender_original: Optional[str] = None,
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
                 content_hash, sender_original)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, original_filename, new_filename, destination_folder,
             onedrive_path, document_type, document_date, sender, recipient,
             company, keywords, extracted_text, matched_rule, tax_relevant,
             email_source, email_from, email_subject, email_date, email_message_id,
             content_hash, sender_original),
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


# ── Settings: persons and sender groups ───────────────────────────────────────
# Edited on the Einstellungen page; read by the bot (filenames), the KI-Suche and
# the ablage-mcp server. Seeded once from classification_rules.yaml.

def _clean_list(values: Any) -> list[str]:
    """Unique, non-empty, whitespace-normalised strings (keeps the first spelling)."""
    if isinstance(values, str):
        values = values.split(",")
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        s = " ".join(str(v).split())
        if s and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out


def parse_settings_yaml(cfg: dict) -> tuple[list[dict], list[dict]]:
    """Read persons and sender groups from any supported YAML layout.

    Supports classification_rules.yaml (nl_family_members, nl_hints), the
    ablage-mcp aliases.yaml (senders, recipients) and the export format
    (persons, sender_groups).
    """
    persons: list[dict] = []
    groups: list[dict] = []
    for m in cfg.get("nl_family_members") or []:
        if m.get("name"):
            persons.append({"name": m["name"], "variants": m.get("aliases") or []})
    for r in cfg.get("recipients") or []:
        if r.get("name"):
            first = str(r["name"]).split()[0]
            persons.append({"name": first, "variants": [r["name"], *(r.get("match") or [])]})
    for p in cfg.get("persons") or []:
        if p.get("name"):
            persons.append({"name": p["name"], "variants": p.get("variants") or []})

    for h in cfg.get("nl_hints") or []:
        if h.get("sender"):
            groups.append({"name": h["sender"], "search_terms": h.get("triggers") or [],
                           "document_type": h.get("type"), "note": h.get("note")})
    for s in cfg.get("senders") or []:
        if s.get("name"):
            groups.append({"name": s["name"], "variants": s.get("match") or []})
    for g in cfg.get("sender_groups") or []:
        if g.get("name"):
            groups.append(dict(g))
    return persons, groups


def _merge_settings(conn: sqlite3.Connection, persons: list[dict], groups: list[dict]) -> dict:
    """Add persons/groups, merging variants into existing entries with the same name."""
    stats = {"persons_added": 0, "persons_updated": 0, "groups_added": 0, "groups_updated": 0}
    for p in persons:
        name = " ".join(str(p["name"]).split())
        row = conn.execute("SELECT id, variants FROM persons WHERE lower(name) = lower(?)", (name,)).fetchone()
        new = [v for v in _clean_list(p.get("variants")) if v.lower() != name.lower()]
        if row is None:
            order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM persons").fetchone()[0]
            conn.execute("INSERT INTO persons(name, variants, sort_order) VALUES (?, ?, ?)",
                         (name, json.dumps(new, ensure_ascii=False), order))
            stats["persons_added"] += 1
        else:
            old = json.loads(row["variants"] or "[]")
            merged = _clean_list(old + new)
            if merged != old:
                conn.execute("UPDATE persons SET variants = ? WHERE id = ?",
                             (json.dumps(merged, ensure_ascii=False), row["id"]))
                stats["persons_updated"] += 1
    for g in groups:
        name = " ".join(str(g["name"]).split())
        variants = [v for v in _clean_list(g.get("variants")) if v.lower() != name.lower()]
        terms = _clean_list(g.get("search_terms"))
        # Merge into an existing group that already knows this name or one of its spellings
        # (e.g. imported "Allianz Suisse" with spelling "Allianz" joins the existing "Allianz").
        incoming = {name.lower(), *(v.lower() for v in variants)}
        row = None
        for r in conn.execute("SELECT * FROM sender_groups ORDER BY id"):
            known = {r["name"].lower(), *(v.lower() for v in json.loads(r["variants"] or "[]"))}
            if r["name"].lower() == name.lower() or incoming & known:
                row = r
                break
        if row is not None and row["name"].lower() != name.lower():
            variants = _clean_list([name, *variants])
            variants = [v for v in variants if v.lower() != row["name"].lower()]
        if row is None:
            conn.execute(
                "INSERT INTO sender_groups(name, variants, search_terms, document_type, note) VALUES (?, ?, ?, ?, ?)",
                (name, json.dumps(variants, ensure_ascii=False), json.dumps(terms, ensure_ascii=False),
                 g.get("document_type") or None, g.get("note") or None),
            )
            stats["groups_added"] += 1
        else:
            old_v, old_t = json.loads(row["variants"] or "[]"), json.loads(row["search_terms"] or "[]")
            mv, mt = _clean_list(old_v + variants), _clean_list(old_t + terms)
            dt = row["document_type"] or g.get("document_type") or None
            note = row["note"] or g.get("note") or None
            if (mv, mt, dt, note) != (old_v, old_t, row["document_type"], row["note"]):
                conn.execute(
                    "UPDATE sender_groups SET variants = ?, search_terms = ?, document_type = ?, note = ? WHERE id = ?",
                    (json.dumps(mv, ensure_ascii=False), json.dumps(mt, ensure_ascii=False), dt, note, row["id"]),
                )
                stats["groups_updated"] += 1
    return stats


def _seed_settings_from_yaml(conn: sqlite3.Connection) -> None:
    """Fill persons and sender groups from classification_rules.yaml on the very first start."""
    cur = conn.execute("INSERT OR IGNORE INTO app_meta(key, value) VALUES ('settings_seeded', datetime('now'))")
    conn.commit()
    if cur.rowcount != 1:
        return  # already seeded (by this or another container)
    try:
        import yaml
        rules_file = os.getenv("CLASSIFICATION_RULES_FILE", "classification_rules.yaml")
        with open(rules_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        persons, groups = parse_settings_yaml(cfg)
        _merge_settings(conn, persons, groups)
        conn.commit()
    except Exception as e:
        print(f"Warning   : could not import settings from classification rules: {e}")


def _person_row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "variants": json.loads(r["variants"] or "[]")}


def _group_row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "name": r["name"], "variants": json.loads(r["variants"] or "[]"),
            "search_terms": json.loads(r["search_terms"] or "[]"),
            "document_type": r["document_type"], "note": r["note"]}


def get_persons() -> list[dict]:
    with _conn() as conn:
        return [_person_row(r) for r in conn.execute("SELECT * FROM persons ORDER BY sort_order, id")]


def get_sender_groups() -> list[dict]:
    with _conn() as conn:
        return [_group_row(r) for r in conn.execute("SELECT * FROM sender_groups ORDER BY name COLLATE NOCASE")]


def _validate_names(items: list[dict], what: str) -> None:
    seen: set[str] = set()
    for it in items:
        name = " ".join(str(it.get("name") or "").split())
        if not name:
            raise ValueError(f"{what}: Name darf nicht leer sein")
        if name.lower() in seen:
            raise ValueError(f"{what}: '{name}' kommt doppelt vor")
        seen.add(name.lower())


def save_persons(persons: list[dict]) -> None:
    """Replace the person list (order = list order)."""
    _validate_names(persons, "Personen")
    with _conn() as conn:
        conn.execute("DELETE FROM persons")
        for i, p in enumerate(persons):
            name = " ".join(str(p["name"]).split())
            variants = [v for v in _clean_list(p.get("variants")) if v.lower() != name.lower()]
            conn.execute("INSERT INTO persons(name, variants, sort_order) VALUES (?, ?, ?)",
                         (name, json.dumps(variants, ensure_ascii=False), i))


def save_sender_groups(groups: list[dict]) -> None:
    """Replace all sender groups."""
    _validate_names(groups, "Absender-Gruppen")
    with _conn() as conn:
        conn.execute("DELETE FROM sender_groups")
        for g in groups:
            name = " ".join(str(g["name"]).split())
            variants = [v for v in _clean_list(g.get("variants")) if v.lower() != name.lower()]
            conn.execute(
                "INSERT INTO sender_groups(name, variants, search_terms, document_type, note) VALUES (?, ?, ?, ?, ?)",
                (name, json.dumps(variants, ensure_ascii=False),
                 json.dumps(_clean_list(g.get("search_terms")), ensure_ascii=False),
                 (g.get("document_type") or None), (str(g.get("note") or "").strip() or None)),
            )


def import_settings_yaml(text: str) -> dict:
    """Merge persons/groups from pasted YAML into the settings; returns counts."""
    import yaml
    cfg = yaml.safe_load(text) or {}
    if not isinstance(cfg, dict):
        raise ValueError("YAML muss ein Objekt mit Schlüsseln wie 'senders' oder 'persons' sein")
    persons, groups = parse_settings_yaml(cfg)
    if not persons and not groups:
        raise ValueError("Keine Personen oder Absender gefunden (erwartet z. B. 'senders', 'recipients', 'persons', 'nl_hints')")
    with _conn() as conn:
        return _merge_settings(conn, persons, groups)


def sender_name_counts() -> list[tuple[str, int]]:
    """Distinct sender names in the archive (sender, else company) with document counts."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(NULLIF(sender,''), NULLIF(company,'')) AS n, COUNT(*) FROM documents "
            "WHERE n IS NOT NULL GROUP BY n ORDER BY 2 DESC"
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


# ── Sender names → group short names ──────────────────────────────────────────

def _starts_with_term(text: str, term: str) -> bool:
    """True if text begins with term as whole word(s), case-insensitive.

    Only the start counts: "Allianz Suisse AG" belongs to Allianz, but "Raiffeisenbank Bern"
    does not belong to Bern. Other spellings are added to the group as variants.
    """
    import re
    return re.match(rf"{re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def match_sender_group(value: Optional[str], groups: list[dict]) -> Optional[str]:
    """Short name of the sender group that value belongs to, or None.

    Value must equal the group name or one of its spellings, or start with one
    ("Allianz Suisse Lebensversicherungs-Gesellschaft AG" → Allianz). An exact spelling
    wins, then the longest one. If two groups match equally well, None is returned.
    """
    v = " ".join(str(value or "").split())
    if not v:
        return None
    best: dict[str, int] = {}  # group name → score
    for g in groups:
        for t in [g["name"], *(g.get("variants") or [])]:
            t = " ".join(t.split())
            if not t:
                continue
            if t.lower() == v.lower():
                score = 10_000 + len(t)
            elif _starts_with_term(v, t):
                score = len(t)
            else:
                continue
            best[g["name"]] = max(best.get(g["name"], 0), score)
    if not best:
        return None
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None
    return ranked[0][0]


def canonical_sender(sender: Optional[str], company: Optional[str], groups: list[dict]) -> Optional[str]:
    """Group short name for a document, from its sender, else its company."""
    return match_sender_group(sender, groups) or match_sender_group(company, groups)


def sender_normalization_plan(groups: Optional[list[dict]] = None) -> list[dict]:
    """Documents whose sender would change to a group short name, grouped by old → new."""
    groups = get_sender_groups() if groups is None else groups
    plan: dict[tuple[str, str], dict] = {}
    with _conn() as conn:
        rows = conn.execute("SELECT id, sender, company FROM documents").fetchall()
    for r in rows:
        new = canonical_sender(r["sender"], r["company"], groups)
        old = r["sender"] or ""
        if not new or new == old:
            continue
        key = (old, new)
        entry = plan.setdefault(key, {"old": old or f"(leer, Firma: {r['company']})", "new": new, "ids": []})
        entry["ids"].append(r["id"])
    return sorted(plan.values(), key=lambda e: (e["new"].lower(), -len(e["ids"])))


def apply_sender_normalization(
    groups: Optional[list[dict]] = None, only: Optional[list[tuple[str, str]]] = None
) -> int:
    """Set sender to the group short name; the old value goes to sender_original once.

    only: (old, new) pairs from the preview to apply; None applies all.
    """
    plan = sender_normalization_plan(groups)
    if only is not None:
        selected = set(only)
        plan = [e for e in plan if (e["old"], e["new"]) in selected]
    changed = 0
    with _conn() as conn:
        for entry in plan:
            for doc_id in entry["ids"]:
                conn.execute(
                    "UPDATE documents SET sender_original = COALESCE(sender_original, sender), sender = ? "
                    "WHERE id = ?",
                    (entry["new"], doc_id),
                )
                changed += 1
    return changed
