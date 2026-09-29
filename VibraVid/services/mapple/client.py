# 29.09.26

from VibraVid.player.mapple import MappleResolver
from VibraVid.player.mapple import get_player_url as _get_player_url


def get_player_url(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> str:
    return _get_player_url(tmdb_id, media_type, season, episode)


def player_is_available(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
) -> bool:
    return MappleResolver().player_is_available(
        tmdb_id,
        media_type,
        season,
        episode,
    )


def get_stream(
    tmdb_id: int,
    media_type: str,
    season: int | None = None,
    episode: int | None = None,
):
    resolved = MappleResolver().resolve_stream(
        tmdb_id,
        media_type,
        season,
        episode,
    )
    return resolved.url, resolved.headers, []
