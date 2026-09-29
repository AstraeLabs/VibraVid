# 29.09.26

import logging
from dataclasses import dataclass, field
from typing import Any

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

PLAYER_REFERER = "https://player.cinezo.live/"


@dataclass
class CinezoSourceProbe:
    """Non-media diagnostic result for one Cinezo source backend."""

    name: str
    endpoint: str
    status_code: int | None = None
    content_type: str | None = None
    available: bool = False
    source_shape: str | None = None
    source_keys: list[str] = field(default_factory=list)
    subtitle_count: int = 0
    error: str | None = None


class CinezoSourceResolver:
    """Inspect one current Cinezo source backend without exposing media URLs."""

    name = ""
    timeout = 20

    def build_endpoint(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> str:
        raise NotImplementedError

    def _availability(self, data: dict[str, Any]) -> tuple[bool, str | None, list[str]]:
        source = data.get("source")

        if isinstance(source, dict):
            keys = sorted(source.keys())
            return bool(source.get("url")), "source", keys

        return False, None, []

    def probe(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> CinezoSourceProbe:
        endpoint = self.build_endpoint(tmdb_id, media_type, season, episode)
        result = CinezoSourceProbe(name=self.name, endpoint=endpoint)
        headers = {
            "user-agent": get_userAgent(),
            "referer": PLAYER_REFERER,
        }

        try:
            with create_client(headers=headers) as client:
                response = client.get(endpoint, timeout=self.timeout)
        except Exception as error:
            result.error = f"{type(error).__name__}: {error}"
            return result

        result.status_code = response.status_code
        result.content_type = response.headers.get("content-type")

        if not response.ok:
            result.error = f"HTTP {response.status_code}"
            return result

        try:
            data = response.json()
        except Exception as error:
            result.error = f"invalid JSON: {type(error).__name__}"
            return result

        if not isinstance(data, dict):
            result.error = f"unexpected JSON payload: {type(data).__name__}"
            return result

        available, source_shape, source_keys = self._availability(data)
        result.available = available
        result.source_shape = source_shape
        result.source_keys = source_keys
        result.subtitle_count = len(data.get("subtitles") or [])

        return result


class ZendayaResolver(CinezoSourceResolver):
    name = "zendaya"

    def build_endpoint(self, tmdb_id, media_type, season=None, episode=None) -> str:
        if media_type == "movie":
            return (
                f"https://proxy3.flikhub.net/movie?id={int(tmdb_id)}"
                "&mode=json&sources=zendaya&hevc=1"
            )
        if media_type == "tv":
            if season is None or episode is None:
                raise ValueError("[Cinezo] season and episode are required for TV source probes")
            return (
                f"https://proxy3.flikhub.net/tv?id={int(tmdb_id)}"
                f"&season={int(season)}&episode={int(episode)}"
                "&mode=json&sources=zendaya&hevc=1"
            )
        raise ValueError(f"[Cinezo] Unsupported media type: {media_type}")


class BerlinResolver(CinezoSourceResolver):
    name = "berlin"

    def build_endpoint(self, tmdb_id, media_type, season=None, episode=None) -> str:
        if media_type == "movie":
            return (
                f"https://proxy1.flikhub.net/movie?id={int(tmdb_id)}"
                "&mode=json&sources=berlin&hevc=1"
            )
        if media_type == "tv":
            if season is None or episode is None:
                raise ValueError("[Cinezo] season and episode are required for TV source probes")
            return (
                f"https://proxy1.flikhub.net/tv?id={int(tmdb_id)}"
                f"&season={int(season)}&episode={int(episode)}"
                "&mode=json&sources=berlin&hevc=1"
            )
        raise ValueError(f"[Cinezo] Unsupported media type: {media_type}")


class JenniferResolver(CinezoSourceResolver):
    name = "jennifer"

    def build_endpoint(self, tmdb_id, media_type, season=None, episode=None) -> str:
        if media_type == "movie":
            return f"https://media.vidcool.net/movie/{int(tmdb_id)}.json"
        if media_type == "tv":
            if season is None or episode is None:
                raise ValueError("[Cinezo] season and episode are required for TV source probes")
            return (
                f"https://media.vidcool.net/tv/{int(tmdb_id)}/"
                f"{int(season)}/{int(episode)}.json"
            )
        raise ValueError(f"[Cinezo] Unsupported media type: {media_type}")

    def _availability(self, data: dict[str, Any]) -> tuple[bool, str | None, list[str]]:
        stream = data.get("stream")
        if isinstance(stream, dict):
            keys = sorted(stream.keys())
            if stream.get("original") or stream.get("hls"):
                return True, "stream", keys

        source = data.get("source")
        if isinstance(source, dict):
            keys = sorted(source.keys())
            if source.get("url"):
                return True, "source", keys

        if data.get("url"):
            return True, "top-level", sorted(data.keys())

        return False, None, []


class CinefreakResolver(CinezoSourceResolver):
    name = "cinefreak"

    def build_endpoint(self, tmdb_id, media_type, season=None, episode=None) -> str:
        if media_type == "movie":
            return (
                f"https://proxy1.flikhub.net/movie?id={int(tmdb_id)}"
                "&mode=json&sources=cinefreak&hevc=1"
            )
        if media_type == "tv":
            if season is None or episode is None:
                raise ValueError("[Cinezo] season and episode are required for TV source probes")
            return (
                f"https://proxy1.flikhub.net/tv?id={int(tmdb_id)}"
                f"&season={int(season)}&episode={int(episode)}"
                "&mode=json&sources=cinefreak&hevc=1"
            )
        raise ValueError(f"[Cinezo] Unsupported media type: {media_type}")


def default_resolvers() -> list[CinezoSourceResolver]:
    """Return source backends in the preference order used by the current player."""
    return [
        ZendayaResolver(),
        BerlinResolver(),
        JenniferResolver(),
        CinefreakResolver(),
    ]


class CinezoResolverChain:
    """Probe current Cinezo source backends in player preference order."""

    def __init__(self, resolvers: list[CinezoSourceResolver] | None = None):
        self.resolvers = list(resolvers) if resolvers is not None else default_resolvers()

    def probe_sources(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[CinezoSourceProbe]:
        results = []

        for resolver in self.resolvers:
            try:
                result = resolver.probe(
                    tmdb_id=tmdb_id,
                    media_type=media_type,
                    season=season,
                    episode=episode,
                )
            except Exception as error:
                logger.warning(f"[Cinezo] Resolver {resolver.name} probe failed: {error}")
                try:
                    endpoint = resolver.build_endpoint(tmdb_id, media_type, season, episode)
                except Exception:
                    endpoint = ""
                result = CinezoSourceProbe(
                    name=resolver.name,
                    endpoint=endpoint,
                    error=f"{type(error).__name__}: {error}",
                )

            results.append(result)

        return results

    def first_available(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> CinezoSourceProbe | None:
        for result in self.probe_sources(tmdb_id, media_type, season, episode):
            if result.available:
                return result
        return None
