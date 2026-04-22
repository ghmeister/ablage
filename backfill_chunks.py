#!/usr/bin/env python3
"""
One-time script: chunk and embed extracted_text for all existing documents.

Run inside the container:
    docker exec <ablage-container-name> python backfill_chunks.py

Documents without stored extracted_text are skipped (re-download not attempted).
Already-chunked documents are skipped automatically.
Safe to re-run: uses ON DELETE CASCADE + replaces existing chunks.
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv()

import db as _db
from embed import chunk_text, get_embedding

_db.init_db()

api_key = os.getenv("OPENAI_API_KEY", "")
if not api_key:
    print("OPENAI_API_KEY not set.")
    sys.exit(1)

docs = _db.get_docs_without_chunks()
print(f"{len(docs)} documents need chunking.")

if not docs:
    print("Nothing to do.")
    sys.exit(0)

errors = 0
for doc in tqdm(docs, unit="doc"):
    text = doc.get("extracted_text") or ""
    if not text.strip():
        continue
    try:
        chunks = chunk_text(text)
        vectors = [get_embedding(c, api_key) for c in chunks]
        _db.store_chunks(doc["id"], chunks, vectors)
    except Exception as e:
        tqdm.write(f"✗ {doc.get('new_filename', doc['id'])}: {e}")
        errors += 1

print(f"\nDone. {len(docs) - errors} chunked, {errors} errors.")
