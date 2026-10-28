#!/usr/bin/env python3
"""
Re-read long PDFs completely and store more text than the 10,000 characters / 10 pages
that the bot and index_existing.py keep.

Why: the MCP server and the KI search only see what is in documents.extracted_text. A yearly
statement or a long contract is cut off after the first pages, so later content is neither
readable nor searchable. For the chosen documents this script
  1. fetches the PDF (OneDrive via Graph, or a local archive folder with --archive-root),
  2. extracts the text again with a higher page limit (text layer first, then Tesseract OCR),
  3. replaces extracted_text if the new text is longer (the FTS index follows through the DB triggers),
  4. re-chunks and re-embeds the text so semantic search finds the later pages too.

Only extracted_text and the chunk/vector tables of the chosen documents change; sender, date,
type and every other field stay as they are. Nothing in OneDrive is touched. Identity documents
are skipped unless --include-ids.

Default is a DRY RUN: it downloads and reads, prints what it would store, writes nothing.
--apply writes and first takes a backup next to the DB (<db>.bak-<timestamp>).

Run inside the bot or web container (needs the Graph settings or ARCHIVE_ROOT, and OPENAI_API_KEY for the vectors):
    python reextract_full.py                       # list documents whose stored text hit the 10,000 limit
    python reextract_full.py --ids 123             # dry run for one document
    python reextract_full.py --ids 123 --apply     # store the full text
    python reextract_full.py --all-candidates      # dry run for every listed document
Scanned PDFs are OCRed page by page (slow: roughly a few seconds per page); --max-pages limits that.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

import db as _db
from backfill_ocr import Fetcher, MissingPDF, backup_db, extract_text, is_id_document, refresh_vectors

LIMIT_HIT = 9_500  # stored texts at or above this length were almost certainly cut at 10,000


def select(ids: list[int], threshold: int) -> list[dict]:
    sql = "SELECT * FROM documents WHERE coalesce(onedrive_path,'') != ''"
    params: list = []
    if ids:
        sql += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    else:
        sql += " AND length(coalesce(extracted_text,'')) >= ?"
        params.append(threshold)
    with _db._conn() as conn:
        return [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]


def main() -> None:
    ap = argparse.ArgumentParser(description="Re-read long PDFs completely and store the full text")
    ap.add_argument("--apply", action="store_true", help="write to the DB (default: dry run)")
    ap.add_argument("--db", metavar="PATH", help="path to documents.db (overrides DB_PATH)")
    ap.add_argument("--ids", type=int, nargs="+", default=[], help="process these document ids")
    ap.add_argument("--all-candidates", action="store_true",
                    help="process every document whose stored text hit the limit (see the list)")
    ap.add_argument("--threshold", type=int, default=LIMIT_HIT, help="stored length that counts as 'cut off'")
    ap.add_argument("--archive-root", metavar="DIR", help="read PDFs from a local archive folder instead of Graph")
    ap.add_argument("--onedrive-prefix", metavar="PATH", help="prefix of stored paths (default OUTPUT_BASE_FOLDER)")
    ap.add_argument("--include-ids", action="store_true", help="also process identity documents (not recommended)")
    ap.add_argument("--max-pages", type=int, default=150, help="pages to read per PDF")
    ap.add_argument("--max-chars", type=int, default=400_000, help="characters to store per document")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--lang", default=os.getenv("TESSERACT_LANGS", "deu+eng"))
    ap.add_argument("--no-embed", action="store_true", help="skip chunks and vectors (run backfill_chunks.py later)")
    ap.add_argument("--no-backup", action="store_true", help="do not back up the DB before --apply")
    args = ap.parse_args()

    if args.db:
        _db.set_db_path(args.db)
    _db.init_db()

    docs = select(args.ids, args.threshold)
    skipped = [d for d in docs if not args.include_ids and is_id_document(d)]
    docs = [d for d in docs if d not in skipped]

    if not args.ids and not args.all_candidates:
        print(f"{len(docs)} documents have a stored text of at least {args.threshold} characters "
              f"(probably cut off at 10,000):")
        for d in docs:
            print(f"  id={d['id']} {d['new_filename']!r} ({d['document_date'] or 'no date'}, "
                  f"{len(d['extracted_text'] or '')} chars)")
        if skipped:
            print(f"  ({len(skipped)} identity documents left out)")
        print("Choose with --ids ... or take all with --all-candidates. Add --apply to write.")
        return

    api_key = os.getenv("OPENAI_API_KEY", "")
    if args.apply and not args.no_embed and not api_key:
        sys.exit("OPENAI_API_KEY not set. Use --no-embed to skip the vectors.")

    mode = "APPLY" if args.apply else "DRY RUN (nothing is written)"
    print(f"{mode}: {len(docs)} documents, up to {args.max_pages} pages / {args.max_chars} characters each.")
    if not docs:
        return
    if args.apply and not args.no_backup:
        print(f"Backup written: {backup_db()}")

    fetcher = Fetcher(args)
    stats = dict(grown=0, same=0, errors=0, missing=0)
    for n, doc in enumerate(docs, 1):
        label = f"[{n}/{len(docs)}] id={doc['id']} {doc['new_filename']!r}"
        old = doc.get("extracted_text") or ""
        try:
            text, total_pages, method = extract_text(fetcher.fetch(doc["onedrive_path"]), args)
            text = text[:args.max_chars]
            read_pages = min(total_pages, args.max_pages)
            if len(text) <= len(old) + 200:
                print(f"{label}: no more text found ({len(text)} chars, {total_pages} p.)")
                stats["same"] += 1
                continue
            note = "" if read_pages == total_pages else f"  NOTE: only the first {read_pages} of {total_pages} pages (raise --max-pages)"
            print(f"{label}: {method}, {read_pages}/{total_pages} p., {len(old)} -> {len(text)} chars{note}")
            if args.apply:
                with _db._conn() as conn:
                    conn.execute("UPDATE documents SET extracted_text = ? WHERE id = ?", (text, doc["id"]))
                if not args.no_embed:
                    try:
                        refresh_vectors(doc["id"], api_key, max_chars=args.max_chars)
                    except Exception as e:  # text is saved; only the vectors are stale
                        print(f"{label}: WARNING text saved, embedding failed: {e}")
            stats["grown"] += 1
        except MissingPDF as e:
            print(f"{label}: PDF not found at the stored path ({e})")
            stats["missing"] += 1
        except Exception as e:  # keep going; one bad PDF must not stop the batch
            print(f"{label}: ERROR {e}")
            stats["errors"] += 1

    print(f"\nDone ({'applied' if args.apply else 'dry run'}): {stats['grown']} with more text, "
          f"{stats['same']} unchanged, {stats['missing']} not found, {stats['errors']} errors.")
    if not args.apply:
        print("Nothing was written. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
