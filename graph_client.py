"""
Microsoft Graph API helper for OneDrive file operations.

Supports two authentication modes:

  1. App-only / Client Credentials (tenant accounts with M365 license)
     Set CLIENT_SECRET in .env.  The app authenticates as itself and accesses
     a specific user's OneDrive via /users/{user_id}/drive.

  2. Delegated / Device Code (personal Microsoft accounts OR tenant accounts)
     Leave CLIENT_SECRET empty.  On first run the user is prompted to visit
     a URL and log in once.  Tokens are cached to disk (.token_cache.json)
     and silently refreshed on every subsequent run.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable, Optional

import msal
import requests

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Scopes for delegated (personal / device-code) access
_DELEGATED_SCOPES = ["Files.ReadWrite", "offline_access"]

# Where to persist the MSAL token cache between runs.
# Override via TOKEN_CACHE_PATH env var (e.g. point to a Docker volume).
import os as _os
_TOKEN_CACHE_PATH = Path(_os.environ.get("TOKEN_CACHE_PATH", ".token_cache.json"))


class GraphAPIError(RuntimeError):
    """Raised when a Graph API request fails."""


class GraphClient:
    """Thin wrapper around Microsoft Graph for OneDrive operations."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: Optional[str] = None,
        user_id: Optional[str] = None,
        scopes: Optional[Iterable[str]] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.session = session or requests.Session()
        self._token: Optional[str] = None
        self._expires_at: float = 0

        self._delegated = not client_secret  # True = personal / device-code flow

        if self._delegated:
            # ----------------------------------------------------------------
            # Delegated auth: PublicClientApplication + device code flow.
            # Works for personal Microsoft accounts and tenant accounts alike.
            # The app acts *as the user*, so drive_base is always /me/drive.
            # ----------------------------------------------------------------
            self.scopes = list(scopes or _DELEGATED_SCOPES)
            self._cache = msal.SerializableTokenCache()
            if _TOKEN_CACHE_PATH.exists():
                self._cache.deserialize(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))

            # personal accounts require the "consumers" authority
            authority = f"https://login.microsoftonline.com/consumers"
            self.app = msal.PublicClientApplication(
                client_id, authority=authority, token_cache=self._cache
            )
            self.drive_base = f"{GRAPH_BASE_URL}/me/drive"
        else:
            # ----------------------------------------------------------------
            # App-only auth: ConfidentialClientApplication + client credentials.
            # Requires an M365-licensed account in the tenant.
            # ----------------------------------------------------------------
            self.scopes = list(scopes or ["https://graph.microsoft.com/.default"])
            self._cache = None
            authority = f"https://login.microsoftonline.com/{tenant_id}"
            self.app = msal.ConfidentialClientApplication(
                client_id, authority=authority, client_credential=client_secret
            )
            if user_id:
                self.drive_base = f"{GRAPH_BASE_URL}/users/{user_id}/drive"
            else:
                self.drive_base = f"{GRAPH_BASE_URL}/me/drive"

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _acquire_token(self, force_refresh: bool = False) -> str:
        if (
            not force_refresh
            and self._token
            and time.time() < self._expires_at - 300
        ):
            return self._token

        if self._delegated:
            result = self._acquire_token_delegated()
        else:
            result = self.app.acquire_token_silent(self.scopes, account=None)
            if not result:
                result = self.app.acquire_token_for_client(scopes=self.scopes)

        if "access_token" not in result:
            raise GraphAPIError(f"Failed to obtain token: {result.get('error_description')}")

        self._token = result["access_token"]
        self._expires_at = time.time() + int(result.get("expires_in", 3600))
        self._persist_cache()
        return self._token

    def _acquire_token_delegated(self) -> dict:
        """Try silent refresh first; fall back to interactive device-code flow."""
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(self.scopes, account=accounts[0])
            if result and "access_token" in result:
                return result

        # No cached token — prompt the user (once ever)
        flow = self.app.initiate_device_flow(scopes=self.scopes)
        if "user_code" not in flow:
            raise GraphAPIError(f"Could not start device flow: {flow}")

        print("\n" + "=" * 60)
        print("ONE-TIME LOGIN REQUIRED")
        print("=" * 60)
        print(flow["message"])   # e.g. "Go to https://microsoft.com/devicelogin and enter code XXXX-XXXX"
        print("=" * 60 + "\n")

        result = self.app.acquire_token_by_device_flow(flow)  # blocks until user logs in
        return result

    def _persist_cache(self):
        """Write the MSAL token cache to disk (delegated mode only)."""
        if self._cache and self._cache.has_state_changed:
            _TOKEN_CACHE_PATH.write_text(self._cache.serialize(), encoding="utf-8")

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        allow_statuses: Optional[set[int]] = None,
        max_retries: int = 3,
        **kwargs,
    ) -> requests.Response:
        """Send an HTTP request with auth, handling 429 + token refresh."""

        allow_statuses = allow_statuses or set()
        last_error: Optional[str] = None

        for attempt in range(max_retries):
            token = self._acquire_token(force_refresh=attempt > 0)
            headers = kwargs.pop("headers", {}) or {}
            headers.setdefault("Authorization", f"Bearer {token}")
            headers.setdefault("Accept", "application/json")
            response = self.session.request(method, url, headers=headers, timeout=30, **kwargs)

            # Rate limiting
            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "5"))
                time.sleep(max(retry_after, 1))
                continue

            # Unauthorized -> retry once with a refreshed token
            if response.status_code in {401, 403} and attempt < max_retries - 1:
                time.sleep(1)
                continue

            if response.status_code in allow_statuses or response.ok:
                return response

            last_error = f"Graph error {response.status_code}: {response.text}"
            time.sleep(2 ** attempt)

        raise GraphAPIError(last_error or "Graph request failed")

    # ------------------------------------------------------------------
    # Delta + file operations
    # ------------------------------------------------------------------

    def get_initial_delta_link(self, folder_id: str) -> str:
        """Walk the initial /delta enumeration to obtain the baseline deltaLink."""
        url = f"{self.drive_base}/items/{folder_id}/delta"
        delta_link: Optional[str] = None
        next_url: Optional[str] = url

        while next_url:
            page = self.get_delta_page(next_url)
            next_url = page.get("@odata.nextLink")
            delta_link = page.get("@odata.deltaLink") or delta_link

        if not delta_link:
            raise GraphAPIError("Could not obtain deltaLink for folder")
        return delta_link

    def get_delta_page(self, url: str) -> dict:
        """Fetch one delta page given a URL (deltaLink or nextLink)."""
        resp = self.request("GET", url)
        return resp.json()

    def download_file(self, item_id: str) -> bytes:
        """Download file content as bytes."""
        url = f"{self.drive_base}/items/{item_id}/content"
        resp = self.request("GET", url, headers={"Accept": "*/*"}, stream=True)
        return resp.content

    def move_and_rename(self, item_id: str, new_name: str, parent_id: str) -> dict:
        """Move an item to parent_id and rename it atomically."""
        url = f"{self.drive_base}/items/{item_id}"
        payload = {"name": new_name, "parentReference": {"id": parent_id}}
        resp = self.request("PATCH", url, json=payload)
        return resp.json()

    def ensure_folder_path(self, path: str) -> str:
        """
        Ensure a folder path exists (relative to drive root) and return its item ID.

        Args:
            path: e.g. "Archive/Rechnungen/2026"
        """
        normalized = path.strip().strip("/").replace("\\", "/")
        if not normalized:
            root_resp = self.request("GET", f"{self.drive_base}/root")
            return root_resp.json()["id"]

        segments = [seg for seg in normalized.split("/") if seg]
        parent_id: str = "root"

        for segment in segments:
            path_url = f"{self.drive_base}/items/{parent_id}/children?$filter=name eq '{segment}'"
            resp = self.request("GET", path_url, allow_statuses={200, 404})
            if resp.status_code == 200:
                items = resp.json().get("value", [])
                match = next((i for i in items if i.get("name") == segment and i.get("folder")), None)
                if match:
                    parent_id = match["id"]
                    continue

            # Not found -> create
            create_url = f"{self.drive_base}/items/{parent_id}/children"
            payload = {"name": segment, "folder": {}}
            created = self.request("POST", create_url, json=payload).json()
            parent_id = created["id"]

        return parent_id
