# 05.10.26

from rich.console import Console
from rich.markup import escape

from VibraVid.core.ui.tracker import context_tracker

from .site_costant import site_constants

console = Console()


def known_tmdb_id(select_title) -> str | None:
    """TMDB id already known for the selected title (no lookup): the one the site supplied, else the trusted match picked for the sidecars."""
    tmdb_id = getattr(select_title, "tmdb_id", None)

    if tmdb_id:
        return str(tmdb_id)

    if context_tracker.sidecar_provider == "tmdb" and context_tracker.sidecar_id:
        return str(context_tracker.sidecar_id)

    return None


def known_series_tmdb_id() -> str | None:
    """TMDB id of the series being downloaded, as already resolved by the base when the title was selected (no lookup)."""
    series_tmdb_id = context_tracker.series_tmdb_id
    return str(series_tmdb_id) if series_tmdb_id else None


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
