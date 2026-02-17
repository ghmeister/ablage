"""
Folder classification module.
Loads rules from a YAML config file and moves renamed PDFs to the correct
archive subfolder based on AI-extracted document metadata.

All executed moves are written to a JSON log file (MOVE_LOG_FILE env var,
default: move_log.json next to this script). Entries are clustered by
target folder and sorted alphabetically for easy auditing.
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


class MoveLogger:
    """
    Persists a log of every file move, clustered and sorted by target folder.

    Log file format (JSON):
    {
      "Rechnungen/2026": [
        {
          "timestamp": "2026-02-17T14:23:01",
          "original_name": "scan001.pdf",
          "original_path": "C:\\...\\from Scanner\\scan001.pdf",
          "new_name": "Rechnung-Amex-2026-01.pdf",
          "destination": "C:\\...\\Rechnungen\\2026\\Rechnung-Amex-2026-01.pdf",
          "document_type": "invoice",
          "company": "American Express",
          "matched_rule": "Rechnungen"
        },
        ...
      ],
      "Versicherung/2025": [ ... ],
      ...
    }
    """

    def __init__(self, log_file: Path):
        self.log_file = log_file
        self._data: dict = self._load()

    def record(
        self,
        original_path: Path,
        new_name: str,
        destination: Path,
        folder_key: str,
        metadata: dict,
        matched_rule: str,
    ):
        """Append one move entry and persist the log."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "original_name": original_path.name,
            "original_path": str(original_path),
            "new_name": new_name,
            "destination": str(destination),
            "document_type": metadata.get("document_type"),
            "company": metadata.get("company"),
            "matched_rule": matched_rule,
        }
        self._data.setdefault(folder_key, []).append(entry)
        self._save()

    def _load(self) -> dict:
        if self.log_file.exists():
            try:
                with open(self.log_file, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save(self):
        # Keep folders sorted alphabetically so the log is easy to browse
        sorted_data = dict(sorted(self._data.items()))
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(sorted_data, f, indent=2, ensure_ascii=False)


class FolderClassifier:
    """Classifies documents and moves them to the correct archive folder."""

    def __init__(self, rules_file: str, output_base_folder: str):
        """
        Initialize the classifier.

        Args:
            rules_file: Path to the YAML rules file
            output_base_folder: Root of the archive (e.g. .../Scanbot/Ablage)
        """
        self.output_base_folder = Path(output_base_folder).resolve()
        if not self.output_base_folder.exists():
            raise ValueError(f"Output base folder does not exist: {output_base_folder}")

        rules_path = Path(rules_file)
        if not rules_path.is_absolute():
            rules_path = Path(__file__).parent / rules_file
        if not rules_path.exists():
            raise ValueError(f"Classification rules file not found: {rules_path}")

        with open(rules_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.unmatched_folder = config.get("unmatched_folder", "Dokumente/Misc")
        self.rules = config.get("rules", [])

        # Move log
        log_file_env = os.getenv("MOVE_LOG_FILE", "move_log.json")
        log_path = Path(log_file_env)
        if not log_path.is_absolute():
            log_path = Path(__file__).parent / log_file_env
        self.logger = MoveLogger(log_path)
        print(f"Move log: {log_path}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, metadata: dict) -> tuple[str, str]:
        """
        Return (destination_folder, matched_rule_name) for a document.

        Args:
            metadata: Dict with keys: document_type, date, company, keywords

        Returns:
            Tuple of (relative folder path, rule name that matched)
        """
        doc_type = (metadata.get("document_type") or "").lower()
        company = (metadata.get("company") or "").lower()
        keywords = [k.lower() for k in (metadata.get("keywords") or [])]

        for rule in self.rules:
            match = rule.get("match", {})

            # 1. document_type match
            rule_types = [t.lower() for t in match.get("document_types", [])]
            if doc_type and doc_type in rule_types:
                return rule["destination"]["folder"], rule["name"]

            # 2. company match (case-insensitive partial)
            rule_companies = [c.lower() for c in match.get("companies", [])]
            if company and any(rc in company or company in rc for rc in rule_companies):
                return rule["destination"]["folder"], rule["name"]

            # 3. keyword overlap
            rule_keywords = [k.lower() for k in match.get("keywords", [])]
            if keywords and any(k in rule_keywords for k in keywords):
                return rule["destination"]["folder"], rule["name"]

        return self.unmatched_folder, "unmatched"

    def move_file(self, current_path: Path, new_name: str, metadata: dict) -> Path:
        """
        Rename and move a PDF to its classified destination folder, then log the move.

        The file is moved directly from its current location to:
            output_base_folder / <folder> / <year> / <new_name>.pdf

        Args:
            current_path: Current path of the PDF file
            new_name: Desired filename without extension
            metadata: Document metadata from AI analysis

        Returns:
            Final path of the moved file
        """
        folder, matched_rule = self.classify(metadata)
        year = self._get_year(metadata)

        dest_dir = self.output_base_folder / folder / year
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest = dest_dir / f"{new_name}.pdf"

        # Handle filename conflicts
        if dest.exists():
            counter = 1
            while dest.exists():
                dest = dest_dir / f"{new_name}_{counter}.pdf"
                counter += 1

        shutil.move(str(current_path), str(dest))

        # Log the move — folder key is the relative path used as cluster heading
        folder_key = f"{folder}/{year}"
        self.logger.record(
            original_path=current_path,
            new_name=dest.name,
            destination=dest,
            folder_key=folder_key,
            metadata=metadata,
            matched_rule=matched_rule,
        )

        return dest

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_year(self, metadata: dict) -> str:
        """Extract the year from metadata['date'] (YYYY-MM-DD), defaulting to current year."""
        date_str = metadata.get("date") or ""
        if date_str:
            try:
                return str(datetime.strptime(date_str[:10], "%Y-%m-%d").year)
            except ValueError:
                pass
        return str(datetime.now().year)
