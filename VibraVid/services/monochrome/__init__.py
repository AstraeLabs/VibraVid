# 16.07.26
# by @danpro00

import logging
from concurrent.futures import ThreadPoolExecutor

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.provider.amazon import amazon_music
from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import base_process_search_result, make_search_entrypoints
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import TVShowManager

from .album import AmazonAlbumScraper
from .downloader import download_song, download_track_from_album

indice = 18
_useFor = "Song"
console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """Search Amazon Music's public catalog for tracks and albums (no auth needed)."""
    entries_manager.clear()
    table_show_manager.clear()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            songs_future = pool.submit(amazon_music.search_songs, query, limit=25)
            albums_future = pool.submit(amazon_music.search_albums, query)
            songs = songs_future.result()
            albums = albums_future.result()
    except Exception as e:
        logger.exception(f"[monochrome] Amazon Music search failed for {query!r}")
        console.print(f"[red]Search failed: {e}")
        return 0

    logger.info(f"[monochrome] query={query!r} -> {len(songs)} song(s), {len(albums)} album(s)")

    for r in songs:
        artist = (r.get("artist") or {}).get("name", "")
        title = r.get("title", "")
        entry = Entries(
            id=r.get("id"),
            name=f"{artist} - {title}" if artist else title,
            type="song",
            year="",
            image=r.get("image", ""),
            url=r["url"],
        )
        entry.title = title
        entry.artist = artist
        entry.album = (r.get("album") or {}).get("name", "")
        entries_manager.add(entry)

    for r in albums:
        artist = (r.get("artist") or {}).get("name", "")
        name = r.get("name", "")
        entry = Entries(
            id=r.get("id"),
            name=f"{artist} - {name}" if artist else name,
            type="album",
            year="",
            image=r.get("image", ""),
            url=r["url"],
        )
        entry.artist = artist
        entry.album = name
        entries_manager.add(entry)

    return len(entries_manager)


def _build_album_scraper(select_title: Entries) -> AmazonAlbumScraper | None:
    """Resolve the album tracklist directly from Amazon Music (no lucida.to involved)."""
    album_id = str(getattr(select_title, "id", "") or "").strip()
    if not album_id:
        console.print(f"[red]Cannot resolve Amazon Music album id for '{select_title.name}'")
        return None

    scraper = AmazonAlbumScraper(album_id)
    try:
        scraper.fetch()
    except Exception as e:
        console.print(f"[red]Failed to fetch album '{select_title.name}': {e}")
        logger.error(f"AmazonAlbumScraper.fetch() error: {e}")
        context_tracker.report_download_error(f"Failed to fetch album '{select_title.name}': {e}")
        return None
    return scraper


def download_series_album(select_title: Entries, season_selection=None, episode_selection=None, scrape_serie=None):
    """Drive the shared season/episode pipeline for a monochrome (Amazon Music) album."""
    start_name = select_title.name if select_title else "Unknown"
    console.print(f"\n[yellow]Download album: [red]{site_constants.SITE_NAME} -> [cyan]{start_name}\n")

    if scrape_serie is None:
        scrape_serie = _build_album_scraper(select_title)
        if scrape_serie is None:
            return (None, False, "Could not load album metadata")

    seasons_count = len(scrape_serie.seasons_manager)

    def _download_episode_callback(season_number: int, download_all: bool, episode_selection=None):
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=lambda ep, sn, ei: download_track_from_album(ep, sn, ei, scrape_serie),
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=_download_episode_callback,
    )

    entries_manager.clear()
    table_show_manager.clear()


def process_search_result(select_title, selections=None, scrape_serie=None):
    """Route songs to download_song (Amazon Music CDN), albums to the series pipeline."""
    if select_title is None:
        console.print("[yellow]No title selected.")
        return False

    media_type = str(getattr(select_title, "type", "")).lower()

    if media_type == "album":
        if scrape_serie is None:
            scrape_serie = _build_album_scraper(select_title)
            if scrape_serie is None:
                return False

        return base_process_search_result(
            select_title=select_title,
            download_series_func=download_series_album,
            media_search_manager=entries_manager,
            table_show_manager=table_show_manager,
            selections=selections,
            scrape_serie=scrape_serie,
        )

    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_song,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


search, _ = make_search_entrypoints(
    title_search=title_search,
    entries_manager=entries_manager,
    table_show_manager=table_show_manager,
    process_result=process_search_result,
)
