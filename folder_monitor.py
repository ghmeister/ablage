"""
Cloud-native OneDrive monitoring using Microsoft Graph delta queries.
"""
from __future__ import annotations

import time
from typing import Callable, Dict

from graph_client import GraphClient


class OneDriveDeltaMonitor:
    """Polls the Graph /delta endpoint for PDF changes."""

    def __init__(
        self,
        graph: GraphClient,
        source_folder_id: str,
        callback: Callable[[Dict], None],
        poll_interval: int = 30,
        skip_existing: bool = True,
    ) -> None:
        self.graph = graph
        self.source_folder_id = source_folder_id
        self.callback = callback
        self.poll_interval = poll_interval
        self.skip_existing = skip_existing
        self.delta_link: str | None = None

    # ------------------------------------------------------------------
    # Delta handling
    # ------------------------------------------------------------------

    def initialize(self):
        """Advance the delta cursor to the current state (skip existing)."""
        self.delta_link = self.graph.get_initial_delta_link(self.source_folder_id)
        print("Initialized delta cursor (existing files skipped).")

    def poll_once(self):
        """Fetch and process one delta cycle (may span multiple pages)."""
        if not self.delta_link:
            self.initialize()

        next_url = self.delta_link
        latest_delta = None

        while next_url:
            page = self.graph.get_delta_page(next_url)
            latest_delta = page.get("@odata.deltaLink") or latest_delta
            next_url = page.get("@odata.nextLink")

            for item in page.get("value", []):
                if item.get("deleted"):
                    continue
                if not item.get("file"):
                    continue
                name = item.get("name", "").lower()
                if not name.endswith(".pdf"):
                    continue
                try:
                    self.callback(item)
                except Exception as exc:  # pragma: no cover - runtime safeguard
                    print(f"Error processing {item.get('name')}: {exc}")

        if latest_delta:
            self.delta_link = latest_delta

    def start(self):
        """Begin polling loop."""
        if self.skip_existing:
            self.initialize()
        else:
            self.delta_link = f"{self.graph.drive_base}/items/{self.source_folder_id}/delta"

        print(f"Polling OneDrive folder (ID={self.source_folder_id}) every {self.poll_interval}s...")

        while True:
            self.poll_once()
            time.sleep(self.poll_interval)
