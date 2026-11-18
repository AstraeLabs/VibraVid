# 07.05.26

import logging
from typing import Any

import requests

from ._base import ArrClient

logger = logging.getLogger("ARR.SONARR")


class SonarrClient(ArrClient):
    """Native Sonarr API v3 client with retry, timeout, and error handling."""

    label = "Sonarr"
    logger = logger
    wanted_params = {"includeSeries": True}
    queue_params = {"includeUnknownSeriesItems": False, "includeSeries": False, "includeEpisode": False}

    # ── series ───────────────────────────────────────────
    def get_series(self) -> list[dict[str, Any]]:
        """Get all series in Sonarr."""
        return self._get("/series").json()

    def get_series_by_id(self, series_id: int) -> dict[str, Any]:
        """Get a single series by ID."""
        return self._get(f"/series/{series_id}").json()

    def series_exists(self, series_id: int) -> bool:
        """Return True if the series is still present in Sonarr."""
        try:
            resp = requests.get(f"{self._base}/series/{series_id}", headers=self._headers, timeout=self.timeout)
            return resp.ok
        except Exception as exc:
            logger.debug(f"Sonarr series {series_id} existence check failed: {exc}")
            return False

    def update_series_path(self, series_id: int, new_path: str) -> bool:
        """Update the root path of a series so Sonarr expects files there."""
        try:
            series = self.get_series_by_id(series_id)
            if series.get("path") == new_path:
                return True
            series["path"] = new_path
            self._put(f"/series/{series_id}", json_data=series)
            logger.info(f"Updated Sonarr series {series_id} path to '{new_path}'")
            return True
        except Exception as exc:
            logger.error(f"Failed to update series path: {exc}")
            return False

    # ── episodes ─────────────────────────────────────────
    def get_episode(self, episode_id: int) -> dict[str, Any]:
        return self._get(f"/episode/{episode_id}").json()

    def get_episodes_for_series(self, series_id: int) -> list[dict[str, Any]]:
        """Get all episodes for a specific series."""
        return self._get("/episode", params={"seriesId": series_id}).json()

    def set_episode_unmonitored(self, episode_ids: list[int]) -> bool:
        """Mark episodes as unmonitored so they disappear from wanted/missing."""
        try:
            self._put("/episode/monitor", json_data={
                    "episodeIds": episode_ids,
                    "monitored": False,
                },
            )
            return True
        except Exception as exc:
            logger.error(f"Failed to set episodes unmonitored: {exc}")
            return False

    # ── queue ────────────────────────────────────────────
    def is_episode_in_queue(self, episode_id: int) -> bool:
        """Check if a specific episode is already downloading."""
        try:
            records = self.queue().get("records", [])
            return any(r.get("episodeId") == episode_id for r in records)
        except Exception:
            return False

    # ── commands ─────────────────────────────────────────
    def command_rescan_series(self, series_id: int) -> dict[str, Any]:
        return self._post("/command", json_data={
                "name": "RescanSeries",
                "seriesId": series_id,
            },
        ).json()

    def command_rename_series(self, series_id: int) -> dict[str, Any]:
        """Ask Sonarr to rename a series' files to its configured naming format."""
        return self._post("/command", json_data={
                "name": "RenameSeries",
                "seriesIds": [series_id],
            },
        ).json()

    def command_series_search(self, series_id: int) -> dict[str, Any]:
        """Trigger a search for all missing episodes of a series."""
        return self._post("/command", json_data={
                "name": "SeriesSearch",
                "seriesId": series_id,
            },
        ).json()

    def manual_import_lookup(self, folder_path: str, series_id: int | None = None) -> list[dict[str, Any]]:
        """Get list of files available for manual import in a folder.

        Returns [] (not raises) if the folder is missing, empty, or Sonarr returns an error.
        """
        params: dict[str, Any] = {"folder": folder_path, "filterExistingFiles": False}
        if series_id:
            params["seriesId"] = series_id
        return self._get_safe("/manualimport", params=params)

    def manual_import(self, import_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit manual import decisions to Sonarr"""
        files = []
        for item in import_items:
            path = str(item.get("path", "")).strip()
            episode_ids = [e["id"] for e in (item.get("episodes") or []) if e.get("id")]
            if not path or not episode_ids:
                continue

            files.append(
                {
                    "path": path,
                    "seriesId": item["seriesId"],
                    "episodeIds": episode_ids,
                    "quality": item.get("quality"),
                    "languages": item.get("languages"),
                    "releaseGroup": item.get("releaseGroup") or "",
                    "indexerFlags": item.get("indexerFlags", 0),
                }
            )

        if not files:
            return {}

        return self._post("/command", json_data={
                "name": "ManualImport",
                "files": files,
                "importMode": "Move",
            },
        ).json()
