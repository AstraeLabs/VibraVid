# 01.10.26

import logging
import os

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

BASE_URL = "https://api4.thetvdb.com/v4"
KIND_TO_TYPE = {"movie": "movie", "tv": "series"}
KIND_TO_PATH = {"movie": "movies", "tv": "series"}


def _configured_api_key() -> str | None:
    """TheTVDB v4 key: TVDB_API_KEY env var first, then Conf/login.json Provider.tvdb."""
    env_key = str(os.environ.get("TVDB_API_KEY") or "").strip()
    if env_key:
        return env_key
    return str(config_manager.login.get("Provider", "tvdb", default="") or "").strip() or None


class TVDBClient:
    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._token: str | None = None
        self._cache: dict = {}

    @property
    def api_key(self) -> str | None:
        return self._api_key if self._api_key is not None else _configured_api_key()

    def _login(self) -> str | None:
        if not self.api_key:
            return None
        try:
            with create_client() as client:
                response = client.post(f"{BASE_URL}/login", json={"apikey": self.api_key}, timeout=30)
            response.raise_for_status()
            self._token = response.json()["data"]["token"]
        except Exception as exc:
            logger.warning(f"TVDB login failed: {exc}")
            self._token = None
        return self._token

    def api_get(self, path: str, params: dict | None = None):
        """The ``data`` payload of a GET (a dict for entities, a list for searches), or None."""
        cache_key = path + str(sorted((params or {}).items()))
        if cache_key in self._cache:
            return self._cache[cache_key]

        token = self._token or self._login()
        for _ in range(2):
            if not token:
                return None
            try:
                with create_client() as client:
                    response = client.get(f"{BASE_URL}{path}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)

                if response.status_code == 401:
                    token = self._login()
                    continue

                if response.status_code == 404:
                    return None
                
                response.raise_for_status()
                data = response.json().get("data")
                self._cache[cache_key] = data
                return data
            except Exception as exc:
                logger.debug(f"TVDB request {path} failed: {exc}")
                return None
        return None

    def search(self, name: str, media_type: str) -> list[dict]:
        """Search results (movie or series) for *name*; the year is left to the caller, TVDB's year filter is exact."""
        params = {"query": name, "limit": 10}
        if media_type in KIND_TO_TYPE:
            params["type"] = KIND_TO_TYPE[media_type]
        return self.api_get("/search", params) or []

    def find_by_remote_id(self, remote_id: str, media_type: str) -> int | None:
        """TVDB id of the movie / series that carries *remote_id* (use an IMDb id: a bare TMDB number is ambiguous)."""
        wanted = KIND_TO_TYPE.get(media_type, "series")
        for hit in self.api_get(f"/search/remoteid/{remote_id}") or []:
            entity = hit.get(wanted)
            if entity and entity.get("id"):
                return int(entity["id"])
        return None

    def extended(self, tvdb_id: int | str, media_type: str, full: bool = False) -> dict | None:
        """The extended record; ``full`` adds characters (cast / crew), companies, content ratings, trailers and artworks."""
        params = None if full else {"short": "true"}
        return self.api_get(f"/{KIND_TO_PATH.get(media_type, 'series')}/{tvdb_id}/extended", params)

    def translation(self, kind: str, tvdb_id: int | str, language: str = "ita") -> dict:
        """Translated name/overview (``kind``: movies, series or episodes); empty when there is none."""
        return self.api_get(f"/{kind}/{tvdb_id}/translations/{language}") or {}

    def episodes(self, series_id: int | str, order: str = "official") -> list[dict]:
        """Every episode of a series in the given season order (aired = ``official``)."""
        episodes: list[dict] = []
        for page in range(20):
            data = self.api_get(f"/series/{series_id}/episodes/{order}", {"page": page})
            if data is None:
                return [] if page else episodes  # a partial listing would hand out wrong numbers
            batch = data.get("episodes") or []
            episodes.extend(batch)
            if len(batch) < 500:
                break
        return episodes


tvdb_client = TVDBClient()
