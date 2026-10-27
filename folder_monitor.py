"""
Cloud-native OneDrive monitoring using Microsoft Graph delta queries.
"""
from __future__ import annotations

import time
from typing import Callable, Dict

from graph_client import GraphClient


# Failed documents are retried after these delays (seconds); after the last
# one the file is left in the drop zone and picked up again on the next start.
RETRY_DELAYS = (60, 300, 900)


class OneDriveDeltaMonitor:
    """Polls the Graph /delta endpoint for PDF changes."""

    def __init__(
        self,
        graph: GraphClient,
        source_folder_id: str,
        callback: Callable[[Dict], None],
        poll_interval: int = 30,
        skip_existing: bool = True,
        process_leftovers: bool = False,
    ) -> None:
        self.graph = graph
        self.source_folder_id = source_folder_id
        self.callback = callback
        self.poll_interval = poll_interval
        self.skip_existing = skip_existing
        # Only safe when processed files leave the drop zone (archive mode);
        # with in-place renaming every file would be processed again.
        self.process_leftovers = process_leftovers
        self.delta_link: str | None = None
        self._failed: Dict[str, dict] = {}   # item id -> {"item", "attempts", "due"}

    # ------------------------------------------------------------------
    # Processing with retries
    # ------------------------------------------------------------------

    def _process(self, item: Dict) -> None:
        item_id = item.get("id", "")
        name = item.get("name")
        try:
            self.callback(item)
            self._failed.pop(item_id, None)
        except Exception as exc:
            attempts = self._failed.get(item_id, {}).get("attempts", 0) + 1
            if attempts > len(RETRY_DELAYS):
                self._failed.pop(item_id, None)
                print(f"Error processing {name}: {exc}")
                print(f"Giving up  : {name} stays in the drop zone and is retried on the next start")
                return
            delay = RETRY_DELAYS[attempts - 1]
            self._failed[item_id] = {"item": item, "attempts": attempts, "due": time.time() + delay}
            print(f"Error processing {name}: {exc}")
            print(f"Retry      : in {delay // 60} min (attempt {attempts + 1} of {len(RETRY_DELAYS) + 1})")

    def _retry_failed(self) -> None:
        now = time.time()
        for item_id, entry in list(self._failed.items()):
            if entry["due"] > now:
                continue
            old_parent = (entry["item"].get("parentReference") or {}).get("id")
            try:
                current = self.graph.get_item(item_id)
            except Exception as exc:
                entry["due"] = now + RETRY_DELAYS[0]
                print(f"Retry check failed for {entry['item'].get('name')}: {exc}")
                continue
            if not current or (current.get("parentReference") or {}).get("id") != old_parent:
                # Deleted, or already moved out of the drop zone
                self._failed.pop(item_id, None)
                continue
            print(f"Retrying   : {current.get('name')}")
            self._process(current)

    def _process_leftovers(self) -> None:
        """Process PDFs still lying in the drop zone (e.g. failed before a restart)."""
        try:
            items = self.graph.list_children(self.source_folder_id)
        except Exception as exc:
            print(f"Warning   : could not list drop zone for leftovers: {exc}")
            return
        pdfs = [i for i in items if i.get("file") and i.get("name", "").lower().endswith(".pdf")]
        if not pdfs:
            return
        print(f"Found {len(pdfs)} unprocessed PDF(s) in the drop zone — processing them now.")
        for item in pdfs:
            self._process(item)

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

        self._retry_failed()

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
                self._process(item)

        if latest_delta:
            self.delta_link = latest_delta

    def start(self):
        """Begin polling loop. Transient errors are retried with exponential backoff."""
        if self.skip_existing:
            self.initialize()
            if self.process_leftovers:
                self._process_leftovers()
        else:
            self.delta_link = f"{self.graph.drive_base}/items/{self.source_folder_id}/delta"

        print(f"Polling OneDrive folder (ID={self.source_folder_id}) every {self.poll_interval}s...")

        backoff = self.poll_interval
        while True:
            try:
                self.poll_once()
                backoff = self.poll_interval  # reset on success
            except Exception as exc:
                print(f"Poll error (retrying in {backoff}s): {exc}")
                backoff = min(backoff * 2, 600)
            time.sleep(backoff)
