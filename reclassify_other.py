#!/usr/bin/env python3
"""
Reclassify documents with document_type='other' using AI on their filename stem.

Since unreadable PDFs often have well-structured filenames already, the AI can
determine the correct type from the filename alone (Rechnung→invoice, etc.).

Usage:
    # Preview what would change (dry run):
    DB_PATH=./documents.db python reclassify_other.py

    # Apply DB updates only:
    DB_PATH=./documents.db python reclassify_other.py --apply

    # Apply DB updates AND physically move files in OneDrive:
    DB_PATH=./documents.db python reclassify_other.py --apply --move
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
from ai_renamer import AIRenamer
from folder_classifier import FolderClassifier


def _full_onedrive_path(onedrive_path: str) -> str:
    """Prepend OUTPUT_BASE_FOLDER if the path is relative to the archive root."""
    archive_root = os.getenv("OUTPUT_BASE_FOLDER", "").strip("/\\")
    if archive_root and not onedrive_path.startswith(archive_root + "/"):
        return f"{archive_root}/{onedrive_path}"
    return onedrive_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify 'other' documents via AI on filename stem"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply changes to DB (default: dry run — print only)"
    )
    parser.add_argument(
        "--move", action="store_true",
        help="Also move files to correct OneDrive folder (requires --apply + Graph env vars)"
    )
    parser.add_argument(
        "--db", metavar="PATH",
        help="Path to documents.db (overrides DB_PATH env var)"
    )
    args = parser.parse_args()

    if args.move and not args.apply:
        print("Error: --move requires --apply")
        sys.exit(1)

    if args.db:
        _db.set_db_path(args.db)

    _db.init_db()

    # AI renamer
    try:
        renamer = AIRenamer()
    except ValueError as e:
        print(f"Error initialising AI renamer: {e}")
        sys.exit(1)

    # Classifier + Graph client (only needed for --move)
    classifier = None
    graph = None
    archive_root = (os.getenv("OUTPUT_BASE_FOLDER") or "").strip("/\\")

    if args.move:
        rules_file = os.getenv("CLASSIFICATION_RULES_FILE", "classification_rules.yaml")
        classifier = FolderClassifier(rules_file)

        tenant_id = os.getenv("TENANT_ID")
        client_id = os.getenv("CLIENT_ID")
        if not (tenant_id and client_id):
            print("Error: --move requires TENANT_ID and CLIENT_ID environment variables")
            sys.exit(1)

        from graph_client import GraphClient
        graph = GraphClient(
            tenant_id, client_id,
            client_secret=os.getenv("CLIENT_SECRET") or None,
            user_id=os.getenv("USER_ID") or None,
        )

    # Fetch all 'other' documents
    with _db._conn() as conn:
        rows = conn.execute(
            "SELECT id, new_filename, document_date, onedrive_path, company, keywords "
            "FROM documents WHERE document_type = 'other' ORDER BY id"
        ).fetchall()

    if not rows:
        print("No documents with type 'other' found.")
        return

    print(f"Found {len(rows)} documents with type 'other'.")
    if not args.apply:
        print("DRY RUN — pass --apply to save changes.\n")
    else:
        print()

    changed = 0
    skipped = 0

    for row in rows:
        doc_id = row["id"]
        filename = row["new_filename"]

        metadata = renamer.classify_from_filename(filename)
        new_type = metadata.get("document_type", "other")

        if new_type == "other":
            print(f"[{doc_id:4d}] {filename}  →  (still 'other', skipped)")
            skipped += 1
            continue

        # Build new destination folder from classifier (even without --move, update DB)
        new_folder = None
        new_onedrive_path = None
        matched_rule = None

        if classifier:
            meta_for_folder = {
                "document_type": new_type,
                "date": row["document_date"] or metadata.get("date"),
            }
            folder, year, matched_rule = classifier.build_destination_path(meta_for_folder)
            rel_path = f"{folder}/{year}" if year else folder
            new_folder = "/".join(filter(None, [archive_root, rel_path]))
        elif os.getenv("OUTPUT_BASE_FOLDER") and os.getenv("CLASSIFICATION_RULES_FILE"):
            # Load classifier on demand if not already loaded
            rules_file = os.getenv("CLASSIFICATION_RULES_FILE", "classification_rules.yaml")
            classifier = FolderClassifier(rules_file)
            meta_for_folder = {
                "document_type": new_type,
                "date": row["document_date"] or metadata.get("date"),
            }
            folder, year, matched_rule = classifier.build_destination_path(meta_for_folder)
            rel_path = f"{folder}/{year}" if year else folder
            new_folder = "/".join(filter(None, [archive_root, rel_path]))

        print(f"[{doc_id:4d}] {filename}  →  {new_type}", end="")
        if new_folder:
            print(f"  ({new_folder})", end="")

        if args.apply:
            updates: dict = {"document_type": new_type}
            if new_folder:
                updates["destination_folder"] = new_folder
            if matched_rule:
                updates["matched_rule"] = matched_rule

            if args.move and graph and row["onedrive_path"]:
                try:
                    item = graph.get_item_by_path(_full_onedrive_path(row["onedrive_path"]))
                    item_id = item["id"]
                    current_filename = item["name"]
                    dest_parent_id = graph.ensure_folder_path(new_folder)
                    result = graph.move_and_rename(item_id, current_filename, dest_parent_id)
                    final_name = result.get("name", current_filename)
                    updates["onedrive_path"] = f"{new_folder}/{final_name}"
                    print(f"  [moved]", end="")
                except Exception as e:
                    print(f"  [move failed: {e}]", end="")

            _db.update_document(doc_id, **updates)
            print("  ✓")
        else:
            print()

        changed += 1

    print(f"\n{'Applied' if args.apply else 'Would change'}: {changed}  |  Skipped: {skipped}")


if __name__ == "__main__":
    main()
