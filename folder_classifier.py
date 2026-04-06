"""
Folder classification module.
Maps AI-determined document types to archive subfolders using a simple
YAML config. The year is always appended as a subfolder.
"""
from datetime import datetime
from pathlib import Path

import yaml


class FolderClassifier:
    """Maps document_type → archive folder using classification_rules.yaml."""

    def __init__(self, rules_file: str):
        rules_path = Path(rules_file)
        if not rules_path.is_absolute():
            rules_path = Path(__file__).parent / rules_file
        if not rules_path.exists():
            raise ValueError(f"Classification rules file not found: {rules_path}")

        with open(rules_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        self.unmatched_folder: str = config.get("unmatched_folder", "Dokumente/Misc")
        self.type_to_folder: dict[str, str] = config.get("type_to_folder", {})

    def build_destination_path(self, metadata: dict) -> tuple[str, str, str]:
        """
        Return (folder, year, matched_rule) for a document.

        Args:
            metadata: Dict with at least 'document_type' and 'date' (YYYY-MM-DD).
        """
        doc_type = (metadata.get("document_type") or "").lower()
        folder = self.type_to_folder.get(doc_type, self.unmatched_folder)
        matched_rule = doc_type if doc_type in self.type_to_folder else "unmatched"
        year = self._get_year(metadata)
        return folder, year, matched_rule

    def _get_year(self, metadata: dict) -> str:
        date_str = metadata.get("date") or ""
        if date_str:
            try:
                return str(datetime.strptime(date_str[:10], "%Y-%m-%d").year)
            except ValueError:
                pass
        return str(datetime.now().year)
