# 06.10.26

import logging
import time
from itertools import count
from typing import Any

import requests


class ArrClient:
    label = "Arr"
    logger = logging.getLogger("ARR")
    wanted_params: dict[str, Any] = {}
    queue_params: dict[str, Any] = {}

    def __init__(self, url: str, api_key: str, timeout: int = 15, max_retries: int = 3):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._base = f"{self.url}/api/v3"
        self._headers = {"X-Api-Key": self.api_key}

    # ── helpers ──────────────────────────────────────────
    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Execute an HTTP request with retry logic."""
        url = f"{self._base}{path}"
        kwargs.setdefault("headers", self._headers)
        kwargs.setdefault("timeout", self.timeout)

        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                self.logger.warning(f"{self.label} request {method} {path} attempt {attempt}/{self.max_retries} failed: {exc}")

        self.logger.error(f"{self.label} request {method} {path} failed after {self.max_retries} attempts")
        raise last_exc

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def _get_safe(self, path: str, params: dict | None = None) -> list[dict[str, Any]]:
        """GET that returns an empty list on any HTTP/network error (no retry)."""
        url = f"{self._base}{path}"
        try:
            resp = requests.get(url, params=params, headers=self._headers, timeout=self.timeout)
            if not resp.ok:
                self.logger.debug(f"{self.label} {path} returned {resp.status_code}, treating as empty")
                return []
            return resp.json()
        except Exception as exc:
            self.logger.debug(f"{self.label} safe GET {path} failed: {exc}")
            return []

    def _post(self, path: str, json_data: dict | None = None) -> requests.Response:
        return self._request("POST", path, json=json_data)

    def _put(self, path: str, json_data: dict | None = None) -> requests.Response:
        return self._request("PUT", path, json=json_data)

    # ── status ───────────────────────────────────────────
    def system_status(self) -> dict[str, Any]:
        """Check connectivity and API key validity."""
        return self._get("/system/status").json()

    def is_available(self) -> bool:
        """Return True if the server is reachable."""
        try:
            self.system_status()
            return True
        except Exception:
            return False

    # ── config ───────────────────────────────────────────
    def get_naming_config(self) -> dict[str, Any]:
        """Get the naming/folder-format configuration."""
        return self._get("/config/naming").json()

    # ── wanted / missing ─────────────────────────────────
    def wanted_missing(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Get missing items (paginated)."""
        return self._get("/wanted/missing", params={**self.wanted_params, "pageSize": page_size, "page": page}).json()

    def get_all_missing(self) -> list[dict[str, Any]]:
        """Iterate all pages and return every missing record."""
        all_records: list[dict[str, Any]] = []
        for page in count(1):
            data = self.wanted_missing(page=page)
            records = data.get("records", [])
            if not records:
                break
            all_records.extend(records)
        return all_records

    # ── queue ────────────────────────────────────────────
    def queue(self) -> dict[str, Any]:
        return self._get("/queue", params=self.queue_params).json()

    # ── tags ─────────────────────────────────────────────
    def get_tags(self) -> list[dict[str, Any]]:
        return self._get("/tag").json()

    def get_tags_map(self) -> dict[int, str]:
        """Return {tag_id: tag_label_lowercase}."""
        try:
            return {t["id"]: t["label"].lower() for t in self.get_tags()}
        except Exception as exc:
            self.logger.error(f"Failed to fetch {self.label} tags: {exc}")
            return {}

    # ── commands ─────────────────────────────────────────
    def get_command(self, command_id: int) -> dict[str, Any]:
        """Poll a queued command's state."""
        return self._get(f"/command/{command_id}").json()

    def wait_command(self, command_id: int, timeout: int = 120) -> str:
        """Block until a command reaches a terminal state; return its status."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self.get_command(command_id).get("status", "")
            except Exception as exc:
                self.logger.debug(f"{self.label} command {command_id} poll failed: {exc}")
                return "unknown"
            if status in ("completed", "failed", "aborted"):
                return status
            time.sleep(1)
        return "timeout"
