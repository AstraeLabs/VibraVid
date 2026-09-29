# 29.09.26

from typing import Any

from VibraVid.player.cinezo import CinezoSourceProbe
from VibraVid.utils.http_client import get_userAgent

PLAYER_REFERER = "https://player.cinezo.live/"


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": get_userAgent(),
        "Referer": PLAYER_REFERER,
    }


def resolve_cinezo_media(
    source: CinezoSourceProbe,
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> dict[str, Any]:
    """Single integration point for the provider-specific Cinezo resolver."""
    if not isinstance(source, CinezoSourceProbe):
        raise TypeError("[Cinezo] Invalid source descriptor")
    if not source.available:
        raise RuntimeError(f"[Cinezo] Source backend is not available: {source.name}")

    media_type = str(media_type or "").lower()
    if media_type not in {"movie", "tv"}:
        raise ValueError(f"[Cinezo] Unsupported media type: {media_type}")
    if media_type == "tv" and (season is None or episode is None):
        raise ValueError("[Cinezo] season and episode are required for TV media resolution")

    context = {
        "source": source.name,
        "source_endpoint": source.endpoint,
        "tmdb_id": int(tmdb_id),
        "media_type": media_type,
        "season": int(season) if season is not None else None,
        "episode": int(episode) if episode is not None else None,
        "headers": _default_headers(),
    }

    # TODO: implement the provider-specific resolver here and return
    # {"url": ..., "headers": ..., "subtitles": ...}.
    raise RuntimeError(
        f"[Cinezo] Media resolver TODO for backend {context['source']} "
        f"(TMDB {context['tmdb_id']}, type {context['media_type']})"
    )
