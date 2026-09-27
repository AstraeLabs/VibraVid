# 16.07.26
# by @danpro00

import logging
import os

from rich.console import Console

from VibraVid.core.downloader import MP4_Downloader
from VibraVid.core.muxing.helper.audio import process_song
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.provider.musiclyric import get_lyrics
from VibraVid.services._base import Entries, site_constants
from VibraVid.services._base.tv_display_manager import map_song_path

from . import amazon
from .amazon import AmazonError

console = Console()
logger = logging.getLogger(__name__)


def _download_via_amazon(select_title) -> str | None:
    """Resolve + download straight from Amazon Music, bypassing lucida.to entirely."""
    title = getattr(select_title, "title", "") or getattr(select_title, "name", "")
    artist = getattr(select_title, "artist", "")
    album = getattr(select_title, "album", "")
    year = getattr(select_title, "year", "")
    cover = getattr(select_title, "image", "")
    track_number = getattr(select_title, "track", None) or None
    duration = getattr(select_title, "duration_seconds", None)
    album_artist = getattr(select_title, "album_artist", "")

    if not duration:
        amazon_id = getattr(select_title, "id", None)
        if amazon_id:
            from VibraVid.provider.amazon import amazon_music

            info = amazon_music.get_track(str(amazon_id))
            if info:
                duration = info.get("duration")
                title = title or info.get("title", "")
                artist = artist or (info.get("artist") or {}).get("name", "")
                album = album or (info.get("album") or {}).get("name", "")

    if not duration:
        logger.info("[monochrome/amazon] no raw duration on entry — cannot match against Amazon Music.")
        return None

    console.print(f"[cyan]Searching on amazon for: [yellow]{artist} - {title}[/yellow]")
    try:
        resp = amazon.get_track_link(
            title=title, duration=int(duration), album=album, artist=artist, quality="UHD"
        )
    except AmazonError as e:
        logger.warning(f"[monochrome/amazon] resolve failed: {e}")
        console.print(f"[yellow]Warning: {e}")
        return None
    except Exception:
        logger.exception(f"[monochrome/amazon] unexpected resolve error for {title!r}")
        return None

    stream_url = amazon.extract_stream_url(resp)
    if not stream_url:
        logger.info(f"[monochrome/amazon] no Amazon Music match for: {artist} - {title} (response keys: {list(resp.keys())})")
        return None

    key_hex = amazon.extract_decryption_key(resp)
    logger.info(f"[monochrome/amazon] match found for {artist} - {title}: stream_url={stream_url} encrypted={bool(key_hex)}")

    path_components, filename = map_song_path(
        artist=artist, album=album, title=title, year=year, track_number=track_number
    )
    dest_base = os.path.join(site_constants.MUSIC_FOLDER, *path_components, filename)

    out_path = f"{dest_base}.m4a"
    logger.info(f"[monochrome/amazon] downloading to: {out_path}")
    result_path, stopped, error = MP4_Downloader(
        url=stream_url,
        path=out_path,
        referer="https://amz.geeked.wtf/",
        key=[f"1:{key_hex}"] if key_hex else None,
        label="Audio",
        check_content_type=False,
        sanitize_path=False,
    )
    if stopped:
        logger.info(f"[monochrome/amazon] download stopped for {title!r}")
        return None
    if not result_path or error:
        logger.warning(f"[monochrome/amazon] download failed for {title!r}: result_path={result_path!r} error={error!r}")
        return None
    logger.info(f"[monochrome/amazon] downloaded: {result_path}")

    context_tracker.report_download_success()

    lyrics_result = None
    try:
        lyrics_result = get_lyrics(title=title, artist=artist, album=album, duration_seconds=duration)
    except Exception:
        logger.error(f"[monochrome/amazon] lyrics lookup crashed for {title!r}")

    final_path = process_song(
        file_path=result_path,
        title=title,
        artist=artist,
        album=album,
        year=year,
        track_number=track_number,
        cover_url=cover,
        album_artist=album_artist,
        lyrics=(lyrics_result or {}).get("lyrics"),
    )
    return final_path


def download_song(select_title) -> str | None:
    """Download a monochrome track via the Amazon Music CDN bypass (see amazon.py)"""
    title = getattr(select_title, "title", "") or getattr(select_title, "name", "")
    try:
        path = _download_via_amazon(select_title)
        if path:
            return path
    except Exception:
        logger.exception(f"[monochrome/amazon] path crashed for {select_title.name!r}")

    message = f"No source found to download '{title}'."
    logger.info(f"[monochrome] {message}")
    console.print(f"[red]{message}")
    context_tracker.report_download_error(message)
    return None


def download_track_from_album(episode_dict, season_number: int, episode_index: int, scrape_serie) -> tuple:
    """Download one track of a monochrome (Amazon Music) album."""
    is_dict = isinstance(episode_dict, dict)
    name = (episode_dict.get("name") if is_dict else getattr(episode_dict, "name", None)) or "Unknown Track"
    track_number = episode_dict.get("number") if is_dict else getattr(episode_dict, "number", None)

    entry = Entries(
        id=episode_dict.get("id") if is_dict else getattr(episode_dict, "id", None),
        name=name,
        type="song",
        url=episode_dict.get("url") if is_dict else getattr(episode_dict, "url", None),
    )
    entry.title = name
    entry.artist = (episode_dict.get("artist") if is_dict else getattr(episode_dict, "artist", "")) or getattr(
        scrape_serie, "artist", ""
    )
    entry.album_artist = getattr(scrape_serie, "artist", "")
    entry.album = getattr(scrape_serie, "title", "")
    entry.year = (episode_dict.get("year") if is_dict else getattr(episode_dict, "year", "")) or getattr(
        scrape_serie, "year", ""
    )
    entry.image = (episode_dict.get("cover") if is_dict else getattr(episode_dict, "cover", "")) or getattr(
        scrape_serie, "cover_url", ""
    )
    entry.track = track_number
    entry.duration_seconds = (
        episode_dict.get("duration_seconds") if is_dict else getattr(episode_dict, "duration_seconds", None)
    )

    path = download_song(entry)
    if path:
        return (path, False, None)
    return (None, False, f"Download failed for '{name}'")
