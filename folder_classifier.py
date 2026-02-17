"""
Folder classification module.
Loads rules from a YAML config file and moves renamed PDFs to the correct
archive subfolder based on AI-extracted document metadata.
"""
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


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
            # Look next to this script if a relative path is given
            rules_path = Path(__file__).parent / rules_file
        if not rules_path.exists():
            raise ValueError(f"Classification rules file not found: {rules_path}")

        with open(rules_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.unmatched_folder = config.get("unmatched_folder", "Dokumente/Misc")
        self.rules = config.get("rules", [])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, metadata: dict) -> str:
        """
        Return the destination folder (relative to output_base_folder) for a document.

        Args:
            metadata: Dict with keys: document_type, date, company, keywords

        Returns:
            Relative folder path string (e.g. "Rechnungen" or "Dokumente/Arzt")
        """
        doc_type = (metadata.get("document_type") or "").lower()
        company = (metadata.get("company") or "").lower()
        keywords = [k.lower() for k in (metadata.get("keywords") or [])]

        for rule in self.rules:
            match = rule.get("match", {})

            # 1. document_type match
            rule_types = [t.lower() for t in match.get("document_types", [])]
            if doc_type and doc_type in rule_types:
                return rule["destination"]["folder"]

            # 2. company match (case-insensitive partial)
            rule_companies = [c.lower() for c in match.get("companies", [])]
            if company and any(rc in company or company in rc for rc in rule_companies):
                return rule["destination"]["folder"]

            # 3. keyword overlap
            rule_keywords = [k.lower() for k in match.get("keywords", [])]
            if keywords and any(k in rule_keywords for k in keywords):
                return rule["destination"]["folder"]

        return self.unmatched_folder

    def move_file(self, current_path: Path, new_name: str, metadata: dict) -> Path:
        """
        Rename and move a PDF to its classified destination folder.

        The file is moved directly from its current location to:
            output_base_folder / <folder> / <year> / <new_name>.pdf

        Args:
            current_path: Current path of the PDF file
            new_name: Desired filename without extension
            metadata: Document metadata from AI analysis

        Returns:
            Final path of the moved file
        """
        folder = self.classify(metadata)
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
