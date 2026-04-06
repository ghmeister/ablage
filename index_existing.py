#!/usr/bin/env python3
"""
index_existing.py — Backfill the SQLite document index from an existing
local OneDrive archive folder.

Usage:
    python index_existing.py <archive_root> [options]

Options:
    --no-ai         Skip AI analysis; parse metadata from filename only.
    --dry-run       Print what would be indexed without writing to the DB.
    --db PATH       Override DB_PATH (or set the DB_PATH env var).
    --limit N       Stop after N documents (useful for testing).

Environment:
    DB_PATH         Path to the SQLite file (default: /data/documents.db).
                    When running locally override this, e.g.:
                        DB_PATH=./documents.db python index_existing.py /path/to/archive
    OPENAI_API_KEY  Required unless --no-ai is set.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill document index from a local archive folder"
    )
    parser.add_argument("archive_root", help="Path to the local OneDrive archive folder")
    parser.add_argument("--no-ai", action="store_true", help="Skip AI; parse metadata from filename")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to the DB")
    parser.add_argument("--db", metavar="PATH", help="Override the DB_PATH")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N documents")
    args = parser.parse_args()

    # Override DB_PATH before importing db so _get_db_path() sees the override
    if args.db:
        os.environ["DB_PATH"] = args.db

    import db as db_module
    from pdf_extractor import PDFExtractor

    if not args.dry_run:
        db_module.init_db()
        print(f"DB: {db_module._get_db_path()}")

    ai_renamer = None
    if not args.no_ai:
        try:
            from ai_renamer import AIRenamer
            ai_renamer = AIRenamer()
            print("AI renamer initialized.")
        except Exception as e:
            print(f"ERROR initialising AI: {e}")
            print("Use --no-ai to skip AI analysis.")
            sys.exit(1)

    extractor = PDFExtractor(max_pages=10)
    archive_root = Path(args.archive_root)

    if not archive_root.is_dir():
        print(f"ERROR: '{archive_root}' is not a directory.")
        sys.exit(1)

    pdf_files = sorted(archive_root.rglob("*.pdf"))
    total_found = len(pdf_files)
    print(f"Found {total_found} PDF files under '{archive_root}'")
    if args.dry_run:
        print("(dry-run mode — nothing will be written to the DB)")

    try:
        from tqdm import tqdm
        iterator = tqdm(pdf_files, unit="file", desc="Indexing")
    except ImportError:
        iterator = pdf_files

    _MAX_TEXT_STORE = 10_000
    processed = skipped = errors = 0

    for pdf_path in iterator:
        if args.limit and processed >= args.limit:
            break

        new_filename = pdf_path.stem

        if not args.dry_run and db_module.document_exists(new_filename):
            skipped += 1
            continue

        try:
            rel = pdf_path.relative_to(archive_root)
        except ValueError:
            rel = pdf_path.name

        onedrive_path = str(rel).replace("\\", "/")
        destination_folder = str(Path(rel).parent).replace("\\", "/")
        if destination_folder == ".":
            destination_folder = ""

        try:
            if args.no_ai:
                metadata = _parse_filename_metadata(new_filename)
                extracted_text = None
            else:
                extracted_text = extractor.extract_text(str(pdf_path))
                if extracted_text:
                    metadata = ai_renamer.analyze_document(extracted_text, new_filename)
                else:
                    print(f"  WARN: no text extracted from {pdf_path.name}; falling back to filename parse")
                    metadata = _parse_filename_metadata(new_filename)
                    extracted_text = None
        except Exception as e:
            print(f"  ERROR processing {pdf_path.name}: {e}")
            errors += 1
            continue

        keywords_str = ", ".join(metadata.get("keywords") or [])
        text_to_store = (extracted_text[:_MAX_TEXT_STORE] if extracted_text else None)

        # Use file creation time as scan_timestamp (falls back to mtime on non-Windows)
        stat = pdf_path.stat()
        file_ts = datetime.fromtimestamp(
            getattr(stat, "st_birthtime", None) or stat.st_mtime,
            tz=timezone.utc,
        ).isoformat(timespec="seconds")

        if args.dry_run:
            print(
                f"  [dry-run] {new_filename} "
                f"| type={metadata.get('document_type')} "
                f"| date={metadata.get('date')} "
                f"| created={file_ts}"
            )
        else:
            try:
                db_module.insert_document(
                    original_filename=pdf_path.name,
                    new_filename=new_filename,
                    destination_folder=destination_folder,
                    onedrive_path=onedrive_path,
                    document_type=metadata.get("document_type"),
                    document_date=metadata.get("date"),
                    sender=metadata.get("sender"),
                    recipient=metadata.get("recipient"),
                    company=metadata.get("company"),
                    keywords=keywords_str,
                    extracted_text=text_to_store,
                    matched_rule="backfill",
                    scan_timestamp=file_ts,
                )
            except Exception as e:
                print(f"  ERROR writing to DB for {pdf_path.name}: {e}")
                errors += 1
                continue

        processed += 1

    print(f"\nDone. Indexed: {processed}  |  Skipped (already in DB): {skipped}  |  Errors: {errors}")


# ---------------------------------------------------------------------------
# Filename parsing for --no-ai mode
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, str] = {
    "rechnung": "invoice",
    "praemienrechnung": "insurance",
    "prämienrechnung": "insurance",
    "versicherungspolice": "insurance",
    "police": "insurance",
    "steuerrechnung": "tax",
    "lohnausweis": "tax",
    "veranlagungsverfuegung": "tax",
    "veranlagungsverfügung": "tax",
    "steuerbescheinigung": "tax",
    "kontoauszug": "bank_statement",
    "depotauszug": "bank_statement",
    "vertrag": "contract",
    "arbeitsvertrag": "contract",
    "mietvertrag": "contract",
    "garantie": "warranty",
    "garantieschein": "warranty",
    "befund": "medical_report",
    "arztbericht": "medical_report",
    "zeugnis": "certificate",
    "diplom": "certificate",
    "offerte": "quote",
    "angebot": "quote",
    "brief": "letter",
}

_KNOWN_PERSONS: set[str] = {"manuel", "judith", "dominik", "clara", "nora"}
_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _parse_filename_metadata(stem: str) -> dict:
    """
    Parse metadata from a structured filename, e.g.:
        Rechnung_Swisscard_Manuel_Details_20260128
    """
    parts = stem.split("_")

    doc_type = "other"
    company: str | None = None
    recipient: str | None = None
    document_date: str | None = None

    if parts:
        doc_type = _TYPE_MAP.get(parts[0].lower(), "other")

    if len(parts) >= 2:
        company = parts[1]

    for part in parts[2:]:
        m = _DATE_RE.match(part)
        if m:
            document_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
        elif part.lower() in _KNOWN_PERSONS:
            recipient = part

    return {
        "filename": stem,
        "document_type": doc_type,
        "date": document_date,
        "company": company,
        "sender": company,
        "recipient": recipient,
        "keywords": [parts[0].lower()] if parts else [],
    }


if __name__ == "__main__":
    _main()
