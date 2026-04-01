#!/usr/bin/env python3
from __future__ import annotations

"""
Cloud-native PDF Renamer Bot using Microsoft Graph delta polling.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from ai_renamer import AIRenamer
from folder_classifier import FolderClassifier
from folder_monitor import OneDriveDeltaMonitor
from graph_client import GraphClient
from pdf_extractor import PDFExtractor
import db as _db

_MAX_TEXT_STORE = 10_000  # characters to store in DB for full-text search


class PDFRenamerBot:
    """Main bot that orchestrates Graph polling, AI naming, and filing."""

    def __init__(self):
        load_dotenv()

        # Required Graph config
        tenant_id = os.getenv("TENANT_ID")
        client_id = os.getenv("CLIENT_ID")
        client_secret = os.getenv("CLIENT_SECRET") or None  # optional: omit for personal OneDrive
        user_id = os.getenv("USER_ID") or None              # optional: only used for app-only mode
        source_folder_id = os.getenv("SOURCE_FOLDER_ID")

        missing = [key for key, val in {
            "TENANT_ID": tenant_id,
            "CLIENT_ID": client_id,
            "SOURCE_FOLDER_ID": source_folder_id,
        }.items() if not val]
        if missing:
            print(f"Missing required environment variables: {', '.join(missing)}")
            print("Please update your .env (see config.example.env)")
            sys.exit(1)

        # Core helpers
        self.pdf_extractor = PDFExtractor(max_pages=int(os.getenv("MAX_PAGES", "10")))

        try:
            self.ai_renamer = AIRenamer()
        except ValueError as e:
            print(f"Error initializing AI renamer: {e}")
            print("Please set your OPENAI_API_KEY in a .env file")
            sys.exit(1)

        self.graph = GraphClient(tenant_id, client_id, client_secret=client_secret, user_id=user_id)
        poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))

        # Optional classification → map to archive path inside OneDrive
        self.archive_root = (os.getenv("OUTPUT_BASE_FOLDER") or "").strip("/\\")
        self.classifier = None
        if self.archive_root:
            rules_file = os.getenv("CLASSIFICATION_RULES_FILE", "classification_rules.yaml")
            try:
                self.classifier = FolderClassifier(rules_file, self.archive_root, allow_missing_base=True)
                print(f"Folder classifier enabled → archive root: {self.archive_root}")
            except Exception as e:
                print(f"Warning: Could not initialize folder classifier: {e}")
                print("Files will be renamed in-place within the source folder.")

        self.monitor = OneDriveDeltaMonitor(
            graph=self.graph,
            source_folder_id=source_folder_id,
            callback=self.process_graph_item,
            poll_interval=poll_interval,
            skip_existing=True,
        )
        _db.init_db()
        print("Document DB initialized.")

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    def process_graph_item(self, item: dict):
        name = item.get("name", "(unknown)")
        item_id = item.get("id")
        parent = item.get("parentReference", {})
        parent_id = parent.get("id")
        parent_path = self._normalize_drive_path(parent.get("path"))

        print(f"\n{'='*60}")
        print(f"Processing: {name}")
        print(f"Source path : {parent_path or '<unknown>'}")
        print(f"{'='*60}")

        # Download + extract in-memory
        content = self.graph.download_file(item_id)
        pdf_text = self.pdf_extractor.extract_text_from_bytes(content)
        if not pdf_text:
            print("Failed to extract text from PDF. Skipping.")
            return

        pdf_info = self.pdf_extractor.get_pdf_info_from_bytes(content)
        print(f"Extracted {len(pdf_text)} characters from PDF ({pdf_info.get('num_pages', 'unknown')} pages)")

        print("\nAnalyzing document with AI...")
        metadata = self.ai_renamer.analyze_document(pdf_text, Path(name).stem)

        new_filename = f"{metadata['filename']}.pdf"
        print(f"Suggested filename : {new_filename}")
        print(f"Document type      : {metadata.get('document_type')}")
        print(f"Date               : {metadata.get('date') or 'not detected'}")
        print(f"Company/sender     : {metadata.get('company') or 'not detected'}")

        dest_parent_id = parent_id
        display_path = parent_path or "<source folder>"
        matched_rule = "n/a"

        if self.classifier:
            folder, year, matched_rule = self.classifier.build_destination_path(metadata)
            rel_path = f"{folder}/{year}" if year else folder
            full_path = "/".join(filter(None, [self.archive_root, rel_path]))
            dest_parent_id = self.graph.ensure_folder_path(full_path)
            display_path = full_path

        result = self.graph.move_and_rename(item_id, new_filename, dest_parent_id)
        final_name = result.get("name", new_filename)
        print(f"\nMoved to  : {display_path}/{final_name}")
        if matched_rule != "n/a":
            print(f"Rule      : {matched_rule}")

        # Write to document index
        try:
            _db.insert_document(
                original_filename=name,
                new_filename=final_name,
                destination_folder=display_path,
                onedrive_path=f"{display_path}/{final_name}",
                document_type=metadata.get("document_type"),
                document_date=metadata.get("date"),
                sender=metadata.get("sender"),
                recipient=metadata.get("recipient"),
                company=metadata.get("company"),
                keywords=", ".join(metadata.get("keywords") or []),
                extracted_text=(pdf_text[:_MAX_TEXT_STORE] if pdf_text else None),
                matched_rule=matched_rule,
            )
            print(f"Indexed   : {final_name}")
        except Exception as e:
            print(f"Warning   : DB write failed: {e}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_drive_path(self, path: str | None) -> str:
        if not path:
            return ""
        # Example Graph path: /drive/root:/Drop Zone
        return path.replace("/drive/root:", "").lstrip("/")

    # ------------------------------------------------------------------
    # Entrypoint
    # ------------------------------------------------------------------

    def run(self):
        print(
            """
╔════════════════════════════════════════════════════════════╗
║              PDF Renamer Bot (Graph)                        ║
║  AI-powered automatic PDF renaming and filing via Graph     ║
╚════════════════════════════════════════════════════════════╝
"""
        )

        print("Starting cloud delta polling...\n")
        self.monitor.start()


def main():
    try:
        bot = PDFRenamerBot()
        bot.run()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
