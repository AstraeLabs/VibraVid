# 05.10.26

from rich.console import Console
from rich.markup import escape

from VibraVid.core.ui.tracker import context_tracker

from .site_costant import site_constants

console = Console()


def _known_match(select_title, media_type: str) -> tuple[str, str] | None:
    """Provider and id already known for the download, without any new lookup."""
    if context_tracker.metadata_provider and context_tracker.metadata_id:
        return context_tracker.metadata_provider, str(context_tracker.metadata_id)

    tmdb_id = getattr(select_title, "tmdb_id", None)
    if tmdb_id:
        return "tmdb", str(tmdb_id)

    if media_type == "tv" and context_tracker.series_tmdb_id:
        return "tmdb", str(context_tracker.series_tmdb_id)

    if context_tracker.sidecar_provider and context_tracker.sidecar_id:
        return context_tracker.sidecar_provider, str(context_tracker.sidecar_id)

    return None


def _resolve_match(select_title, media_type: str) -> tuple[str, str] | None:
    """Like :func:`_known_match`, but searches the configured metadata providers when nothing is known yet."""
    match = _known_match(select_title, media_type)
    if match or select_title is None:
        return match

    from VibraVid.services._base import metadata

    try:
        target = metadata.find_sidecar_target(media_type, select_title)
    except Exception:
        return None
    if not target:
        return None

    context_tracker.metadata_provider = target[0]
    context_tracker.metadata_id = str(target[1])
    return context_tracker.metadata_provider, context_tracker.metadata_id


def _label_id(match: tuple[str, str] | None) -> str | None:
    """A TMDB id is shown bare; another provider's id is tagged so it cannot be mistaken for a TMDB one."""
    if not match:
        return None
    provider, item_id = match
    return item_id if provider == "tmdb" else f"{provider}:{item_id}"


def known_tmdb_id(select_title) -> str | None:
    """Id shown for a film: the site's TMDB id, else the first reliable metadata-provider match."""
    media_type = "movie" if str(getattr(select_title, "type", "")).lower() in ("movie", "film") else None
    if media_type is None:
        tmdb_id = getattr(select_title, "tmdb_id", None)
        return str(tmdb_id) if tmdb_id else None

    return _label_id(_resolve_match(select_title, media_type))


def known_series_tmdb_id() -> str | None:
    """Id shown for a series: the one resolved when the title was selected, else the first reliable provider match."""
    return _label_id(_resolve_match(context_tracker.metadata_title, "tv"))


def print_download_header(select_title, tmdb_id: str | None = None, year=None, suffix: str = "") -> None:
    """The ``Download: SITE -> title (year) [tmdb_id]`` line shown right before a download."""
    tmdb_id = str(tmdb_id) if tmdb_id else known_tmdb_id(select_title)
    year = str(year) if year and str(year) != "9999" else None

    label = f"[cyan]{escape(str(select_title.name))}"
    if year:
        label += f" [white]({escape(year)})"
    if tmdb_id:
        label += f" [magenta]\\[{escape(tmdb_id)}]"
    if suffix:
        label += f" {suffix}"

    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> {label} \n")


def print_episode_header(
    series_name, episode_name=None, season=None, episode=None, tmdb_id: str | None = None, year=None
) -> None:
    """The ``Download: SITE -> series (year) [tmdb_id] [episode_name] (SxxExx)`` line shown right before a download."""
    tmdb_id = str(tmdb_id) if tmdb_id else known_series_tmdb_id()
    year = str(year) if year and str(year) != "9999" else None

    label = f"[cyan]{escape(str(series_name))}"
    if year:
        label += f" [white]({escape(year)})"
    if tmdb_id:
        label += f" [magenta]\\[{escape(tmdb_id)}]"
    if episode_name:
        label += f" [white]\\ [magenta]{escape(str(episode_name))}"
    if episode is not None:
        label += f" ([cyan]S{season}E{episode})" if season is not None else f" ([cyan]E{episode})"

    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> {label} \n")
