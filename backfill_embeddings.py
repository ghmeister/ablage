#!/usr/bin/env python3
"""
Generate and store embeddings for all documents that don't have one yet.

Usage (in container):
    docker exec <container> python backfill_embeddings.py

Usage (local):
    DB_PATH=./documents.db OPENAI_API_KEY=sk-... python backfill_embeddings.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

import db as _db
from embed import build_document_text, get_embedding

_RATE_LIMIT_DELAY = 0.1  # seconds between API calls


def main() -> None:
    db_path = os.getenv("DB_PATH", "/data/documents.db")
    _db.set_db_path(db_path)
    _db.init_db()

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        print("Error: OPENAI_API_KEY not set")
        sys.exit(1)

    if not _db._vec_available:
        print("Error: sqlite-vec not available — install it with: pip install sqlite-vec")
        sys.exit(1)

    docs = _db.get_documents_without_embedding()
    total = len(docs)
    print(f"Documents without embedding: {total}")

    if total == 0:
        print("Nothing to do.")
        return

    ok = 0
    for i, doc in enumerate(docs, 1):
        text = build_document_text(doc)
        name = doc.get("new_filename") or f"id={doc['id']}"
        if not text.strip():
            print(f"  [{i}/{total}] skip (no text): {name}")
            continue
        try:
            vector = get_embedding(text, api_key)
            _db.store_embedding(doc["id"], vector)
            ok += 1
            print(f"  [{i}/{total}] ✓ {name}")
            time.sleep(_RATE_LIMIT_DELAY)
        except Exception as e:
            print(f"  [{i}/{total}] ✗ {name}: {e}")

    print(f"\nDone — {ok}/{total} embeddings stored.")


if __name__ == "__main__":
    main()
