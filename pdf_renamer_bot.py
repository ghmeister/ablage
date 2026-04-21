#!/usr/bin/env python3
from __future__ import annotations

"""
Ablage — AI-powered document archiving bot using Microsoft Graph delta polling.
"""

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from ai_renamer import AIRenamer
from folder_classifier import FolderClassifier
from folder_monitor import OneDriveDeltaMonitor
from graph_client import GraphClient
from pdf_extractor import PDFExtractor
import db as _db

_MAX_TEXT_STORE = 10_000  # characters to store in DB for full-text search
_STATUS_FILE = Path(os.getenv("DB_PATH", "/data/documents.db")).parent / "bot_status.json"
_LOG_FILE    = Path(os.getenv("DB_PATH", "/data/documents.db")).parent / "bot.log"


def _write_status(status: str, filename: str = "") -> None:
    try:
        _STATUS_FILE.write_text(json.dumps({
            "status": status,
            "filename": filename,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }), encoding="utf-8")
    except Exception:
        pass


class _Tee:
    """Write to both stdout and a log file simultaneously, prepending timestamps."""
    def __init__(self, log_path: Path, max_bytes: int = 2 * 1024 * 1024):
        self._log_path = log_path
        self._max_bytes = max_bytes
        self._orig = sys.stdout
        self._at_line_start = True
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(log_path, "a", encoding="utf-8", buffering=1)

    def _stamp(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") + "  "

    def write(self, data: str) -> int:
        self._orig.write(data)
        # Prepend timestamp at the start of each new line
        out = []
        for ch in data:
            if self._at_line_start and ch != "\n":
                out.append(self._stamp())
                self._at_line_start = False
            out.append(ch)
            if ch == "\n":
                self._at_line_start = True
        self._f.write("".join(out))
        # Truncate if log gets too large (keep last 75%)
        try:
            if self._f.tell() > self._max_bytes:
                self._f.close()
                content = self._log_path.read_text(encoding="utf-8", errors="replace")
                keep = content[len(content) // 4:]
                self._log_path.write_text(keep, encoding="utf-8")
                self._f = open(self._log_path, "a", encoding="utf-8", buffering=1)
        except Exception:
            pass
        return len(data)

    def flush(self) -> None:
        self._orig.flush()
        self._f.flush()

    def fileno(self):
        return self._orig.fileno()


class AblageBot:
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
                self.classifier = FolderClassifier(rules_file)
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

        _write_status("processing", name)
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

        content_hash = hashlib.sha256(pdf_text.encode("utf-8", errors="replace")).hexdigest()
        duplicate_id = _db.find_duplicate_by_hash(content_hash)
        if duplicate_id:
            print(f"⚠ Duplicate detected — matches document ID {duplicate_id}. Filing anyway.")

        # Check for email sidecar uploaded by email-pdf-extractor
        email_context = None
        sidecar_item_id = None
        if parent_path:
            sidecar_path = f"{parent_path}/{name}.meta.json"
            try:
                sidecar_item = self.graph.get_item_by_path(sidecar_path)
                sidecar_item_id = sidecar_item.get("id")
                sidecar_bytes = self.graph.download_file(sidecar_item_id)
                email_context = json.loads(sidecar_bytes.decode("utf-8"))
                print(f"Email context      : from={email_context.get('from', '')!r}")
            except Exception:
                pass  # No sidecar — that's fine

        print("\nAnalyzing document with AI...")
        metadata = self.ai_renamer.analyze_document(pdf_text, Path(name).stem, email_context=email_context)

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
        # Store stem without .pdf — the web template appends the extension
        final_stem = final_name[:-4] if final_name.lower().endswith(".pdf") else final_name
        print(f"\nMoved to  : {display_path}/{final_name}")
        if matched_rule != "n/a":
            print(f"Rule      : {matched_rule}")

        # destination_folder stores only the relative path (without OUTPUT_BASE_FOLDER prefix)
        # so it stays consistent with backfill-indexed documents
        db_folder = rel_path if self.classifier else display_path

        # Write to document index
        try:
            _db.insert_document(
                original_filename=name,
                new_filename=final_stem,
                destination_folder=db_folder,
                onedrive_path=f"{display_path}/{final_name}",
                document_type=metadata.get("document_type"),
                document_date=metadata.get("date"),
                sender=metadata.get("sender"),
                recipient=metadata.get("recipient"),
                company=metadata.get("company"),
                keywords=", ".join(metadata.get("keywords") or []),
                extracted_text=(pdf_text[:_MAX_TEXT_STORE] if pdf_text else None),
                matched_rule=matched_rule,
                tax_relevant=1 if metadata.get("tax_relevant") else 0,
                email_source=1 if email_context else 0,
                email_from=email_context.get("from") if email_context else None,
                email_subject=email_context.get("subject") if email_context else None,
                email_date=email_context.get("date") if email_context else None,
                email_message_id="".join(email_context.get("message_id", "").split()).strip("<>") if email_context else None,
                content_hash=content_hash,
            )
            print(f"Indexed   : {final_name}")
        except Exception as e:
            print(f"Warning   : DB write failed: {e}")

        # Delete the sidecar now that it's been consumed
        if sidecar_item_id:
            try:
                self.graph.delete_item(sidecar_item_id)
            except Exception as e:
                print(f"Warning   : Could not delete sidecar: {e}")

        _write_status("idle")

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
║                       Ablage                               ║
║     AI-powered document archiving via Microsoft Graph      ║
╚════════════════════════════════════════════════════════════╝
"""
        )

        print("Starting cloud delta polling...\n")
        self.monitor.start()


def main():
    sys.stdout = _Tee(_LOG_FILE)
    _write_status("idle")
    try:
        bot = AblageBot()
        bot.run()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
