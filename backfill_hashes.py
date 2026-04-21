#!/usr/bin/env python3
"""
backfill_hashes.py — Compute and store content_hash for existing documents.

Reads local PDF files via ARCHIVE_ROOT (or --archive-root), extracts text,
and updates the content_hash column for all documents that are missing one.

Usage:
    python backfill_hashes.py --archive-root /path/to/local/archive
    DB_PATH=./documents.db python backfill_hashes.py --archive-root /mnt/onedrive/Ablage

Options:
    --archive-root PATH   Local path where PDFs are stored (falls back to ARCHIVE_ROOT env var)
    --db PATH             Override DB_PATH
    --dry-run             Print what would be updated without writing to the DB
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _main() -> None:
    parser = argparse.ArgumentParser(description="Backfill content_hash for existing documents")
    parser.add_argument("--archive-root", metavar="PATH", help="Local archive root (or set ARCHIVE_ROOT env var)")
    parser.add_argument("--db", metavar="PATH", help="Override DB_PATH")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to the DB")
    args = parser.parse_args()

    if args.db:
        os.environ["DB_PATH"] = args.db

    archive_root_str = args.archive_root or os.getenv("ARCHIVE_ROOT", "").strip()
    if not archive_root_str:
        print("ERROR: Provide --archive-root or set the ARCHIVE_ROOT environment variable.")
        sys.exit(1)

    archive_root = Path(archive_root_str).resolve()
    if not archive_root.is_dir():
        print(f"ERROR: '{archive_root}' is not a directory.")
        sys.exit(1)

    import db as db_module
    from pdf_extractor import PDFExtractor

    db_module.init_db()
    print(f"DB           : {db_module._get_db_path()}")
    print(f"Archive root : {archive_root}")

    extractor = PDFExtractor(max_pages=10)
    output_base = os.getenv("OUTPUT_BASE_FOLDER", "").strip("/\\")

    # Fetch all docs missing a content_hash
    with db_module._conn() as conn:
        rows = conn.execute(
            "SELECT id, new_filename, onedrive_path FROM documents WHERE content_hash IS NULL"
        ).fetchall()

    total = len(rows)
    print(f"Documents missing hash: {total}")
    if args.dry_run:
        print("(dry-run — nothing will be written)\n")

    updated = skipped = errors = 0

    for row in rows:
        doc_id, new_filename, onedrive_path = row["id"], row["new_filename"], row["onedrive_path"]

        if not onedrive_path:
            print(f"  SKIP #{doc_id} {new_filename} — no onedrive_path stored")
            skipped += 1
            continue

        # Strip OUTPUT_BASE_FOLDER prefix to get path relative to archive_root
        rel = onedrive_path.lstrip("/")
        if output_base and rel.startswith(output_base + "/"):
            rel = rel[len(output_base) + 1:]

        pdf_path = archive_root / rel
        if not pdf_path.is_file():
            print(f"  SKIP #{doc_id} {new_filename} — file not found: {pdf_path}")
            skipped += 1
            continue

        try:
            pdf_text = extractor.extract_text(str(pdf_path))
        except Exception as e:
            print(f"  ERROR #{doc_id} {new_filename} — extraction failed: {e}")
            errors += 1
            continue

        if not pdf_text:
            print(f"  SKIP #{doc_id} {new_filename} — no text extracted")
            skipped += 1
            continue

        content_hash = hashlib.sha256(pdf_text.encode("utf-8", errors="replace")).hexdigest()

        if args.dry_run:
            print(f"  [dry-run] #{doc_id} {new_filename} → {content_hash[:12]}…")
        else:
            db_module.update_document(doc_id, content_hash=content_hash)
            print(f"  ✓ #{doc_id} {new_filename}")

        updated += 1

    print(f"\nDone.  Updated: {updated}  |  Skipped: {skipped}  |  Errors: {errors}")

    # Report any duplicates found
    if not args.dry_run and updated > 0:
        with db_module._conn() as conn:
            dups = conn.execute(
                """
                SELECT content_hash, COUNT(*) as cnt, GROUP_CONCAT(id) as ids
                FROM documents
                WHERE content_hash IS NOT NULL
                GROUP BY content_hash
                HAVING cnt > 1
                """
            ).fetchall()
        if dups:
            print(f"\n⚠ {len(dups)} duplicate group(s) found:")
            for dup in dups:
                print(f"   IDs [{dup['ids']}] — hash {dup['content_hash'][:12]}…")
        else:
            print("\n✓ No duplicates found in existing documents.")


if __name__ == "__main__":
    _main()
