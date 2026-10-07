# 07.05.26

import logging
from typing import Any

import requests

from ._base import ArrClient

logger = logging.getLogger("ARR.RADARR")


class RadarrClient(ArrClient):
    """Native Radarr API v3 client with retry, timeout, and error handling."""

    label = "Radarr"
    logger = logger
    wanted_params = {}
    queue_params = {"includeUnknownMovieItems": False, "includeMovie": False}

    # ── movies ───────────────────────────────────────────
    def get_movies(self) -> list[dict[str, Any]]:
        """Get all movies in Radarr."""
        return self._get("/movie").json()

    def get_movie_by_id(self, movie_id: int) -> dict[str, Any]:
        """Get a single movie by ID."""
        return self._get(f"/movie/{movie_id}").json()

    def movie_exists(self, movie_id: int) -> bool:
        """Return True if the movie is still present in Radarr."""
        try:
            resp = requests.get(f"{self._base}/movie/{movie_id}", headers=self._headers, timeout=self.timeout)
            return resp.ok
        except Exception as exc:
            logger.debug(f"Radarr movie {movie_id} existence check failed: {exc}")
            return False

    def update_movie_path(self, movie_id: int, new_path: str) -> bool:
        """Update the root path of a movie so Radarr expects files there."""
        try:
            movie = self.get_movie_by_id(movie_id)
            if movie.get("path") == new_path:
                return True
            movie["path"] = new_path
            self._put(f"/movie/{movie_id}", json_data=movie)
            logger.info(f"Updated Radarr movie {movie_id} path to '{new_path}'")
            return True
        except Exception as exc:
            logger.error(f"Failed to update movie path: {exc}")
            return False

    def set_movie_unmonitored(self, movie_id: int) -> bool:
        """Mark a movie as unmonitored."""
        try:
            movie_data = self.get_movie_by_id(movie_id)
            movie_data["monitored"] = False
            self._put(f"/movie/{movie_id}", json_data=movie_data)
            return True
        except Exception as exc:
            logger.error(f"Failed to set movie {movie_id} unmonitored: {exc}")
            return False

    # ── queue ────────────────────────────────────────────
    def is_movie_in_queue(self, movie_id: int) -> bool:
        """Check if a specific movie is already downloading."""
        try:
            records = self.queue().get("records", [])
            return any(r.get("movieId") == movie_id for r in records)
        except Exception:
            return False

    # ── commands ─────────────────────────────────────────
    def command_rescan_movie(self, movie_id: int) -> dict[str, Any]:
        return self._post("/command", json_data={
                "name": "RescanMovie",
                "movieId": movie_id,
            },
        ).json()

    def command_rename_movie(self, movie_id: int) -> dict[str, Any]:
        """Ask Radarr to rename a movie's files to its configured naming format."""
        return self._post("/command", json_data={
                "name": "RenameMovie",
                "movieIds": [movie_id],
            },
        ).json()

    def manual_import_lookup(self, folder_path: str, movie_id: int | None = None) -> list[dict[str, Any]]:
        """Get list of files available for manual import in a folder."""
        params: dict[str, Any] = {"folder": folder_path, "filterExistingFiles": False}
        if movie_id:
            params["movieId"] = movie_id
        return self._get_safe("/manualimport", params=params)

    def manual_import(self, import_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit manual import decisions to Radarr."""
        files = []
        for item in import_items:
            path = str(item.get("path", "")).strip()
            if not path:
                continue

            files.append(
                {
                    "path": path,
                    "movieId": item["movieId"],
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
