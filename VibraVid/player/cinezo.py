# 29.09.26

import logging
from dataclasses import dataclass, field
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class CinezoStream:
    """Resolved Cinezo playback information consumed by the service downloader."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)
    subtitles: list[dict] = field(default_factory=list)


class CinezoResolver(Protocol):
    """Interface implemented by one Cinezo source resolver."""

    name: str

    def resolve(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> CinezoStream | None:
        ...


class _PendingResolver:
    """Placeholder for one resolver advertised by the current Cinezo player."""

    name = "pending"

    def resolve(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> CinezoStream | None:
        # TODO: implement this Cinezo source resolver.
        #
        # Keep resolver-specific network and response parsing here. The service
        # client should only orchestrate resolvers and return the normalized
        # CinezoStream structure to downloader.py.
        return None


class ZendayaResolver(_PendingResolver):
    name = "zendaya"


class BerlinResolver(_PendingResolver):
    name = "berlin"


class JenniferResolver(_PendingResolver):
    name = "jennifer"


class CinefreakResolver(_PendingResolver):
    name = "cinefreak"


def default_resolvers() -> list[CinezoResolver]:
    """Return Cinezo resolvers in the current player preference order."""
    return [
        ZendayaResolver(),
        BerlinResolver(),
        JenniferResolver(),
        CinefreakResolver(),
    ]


class CinezoResolverChain:
    """Try Cinezo source resolvers in order and normalize failures."""

    def __init__(self, resolvers: list[CinezoResolver] | None = None):
        self.resolvers = list(resolvers) if resolvers is not None else default_resolvers()

    def resolve(
        self,
        tmdb_id: int,
        media_type: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> CinezoStream | None:
        for resolver in self.resolvers:
            try:
                result = resolver.resolve(
                    tmdb_id=tmdb_id,
                    media_type=media_type,
                    season=season,
                    episode=episode,
                )
            except Exception as error:
                logger.warning(f"[Cinezo] Resolver {resolver.name} failed: {error}")
                continue

            if result is None:
                continue

            if not result.url:
                logger.warning(f"[Cinezo] Resolver {resolver.name} returned an empty URL")
                continue

            logger.info(f"[Cinezo] Resolver {resolver.name} selected")
            return result

        return None
