# 14.10.26

import logging
import os
import shutil
import tempfile

from rich.console import Console

from VibraVid.core.downloader import YTDLPDownloader
from VibraVid.core.muxing.helper.audio import process_song
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.provider.musiclyric import get_lyrics
from VibraVid.services._base import Entries, site_constants
from VibraVid.services._base.tv_display_manager import map_song_path
from VibraVid.setup import get_deno_path

console = Console()
logger = logging.getLogger(__name__)


def _download_via_ytdlp(select_title) -> str | None:
    """Download track using yt-dlp from YouTube Music with progress bar."""
    title = getattr(select_title, "title", "") or getattr(select_title, "name", "")
    artist = getattr(select_title, "artist", "")
    album = getattr(select_title, "album", "")
    year = getattr(select_title, "year", "")
    cover = getattr(select_title, "image", "")
    track_number = getattr(select_title, "track", None) or None
    duration = getattr(select_title, "duration_seconds", None)
    album_artist = getattr(select_title, "album_artist", "")

    video_id = getattr(select_title, "id", "")
    url = getattr(select_title, "url", "") or f"https://music.youtube.com/watch?v={video_id}"

    if not video_id and "youtube.com" not in url and "youtu.be" not in url:
        logger.warning(f"[youtube_music] Invalid YouTube Music URL: {url}")
        return None

    console.print(f"[cyan]Downloading via yt-dlp: [yellow]{artist} - {title}[/yellow]")

    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            downloaded, _stopped, error = YTDLPDownloader(
                url=url,
                output_dir=temp_dir,
                filename="track",
                format_spec="bestaudio[ext=webm]/bestaudio/best",
                extract_audio=True,
                audio_format="opus",
                audio_quality="0",
                no_playlist=True,
                deno_path=get_deno_path(),
            ).start()
            if error or not downloaded:
                logger.warning(f"[youtube_music] No audio file downloaded for {artist} - {title}: {error}")
                return None

            downloaded_file = str(downloaded)
            logger.info(f"[youtube_music] Downloaded to temp: {downloaded_file}")

            path_components, filename = map_song_path(
                artist=artist, album=album, title=title, year=year, track_number=track_number
            )
            dest_base = os.path.join(site_constants.MUSIC_FOLDER, *path_components, filename)

            os.makedirs(os.path.dirname(dest_base), exist_ok=True)

            final_ext = os.path.splitext(downloaded_file)[1] or ".opus"
            dest_path = f"{dest_base}{final_ext}"

            if os.path.exists(dest_path):
                base, ext = os.path.splitext(dest_path)
                counter = 1
                while os.path.exists(f"{base} ({counter}){ext}"):
                    counter += 1
                dest_path = f"{base} ({counter}){ext}"

            shutil.move(downloaded_file, dest_path)
            logger.info(f"[youtube_music] Moved to: {dest_path}")

            context_tracker.report_download_success()

            lyrics_result = None
            try:
                lyrics_result = get_lyrics(title=title, artist=artist, album=album, duration_seconds=duration)
            except Exception:
                logger.error(f"[youtube_music] lyrics lookup crashed for {title!r}")

            final_path = process_song(
                file_path=dest_path,
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

        except Exception as e:
            logger.exception(f"[youtube_music] Download crashed for {title!r}: {e}")
            console.print(f"[red]Download failed: {e}")
            return None


def download_song(select_title) -> str | None:
    """Download a YouTube Music track via yt-dlp."""
    title = getattr(select_title, "title", "") or getattr(select_title, "name", "")
    try:
        path = _download_via_ytdlp(select_title)
        if path:
            return path
    except Exception:
        logger.exception(f"[youtube_music] path crashed for {select_title.name!r}")

    message = f"No source found to download '{title}'."
    logger.info(f"[youtube_music] {message}")
    console.print(f"[red]{message}")
    context_tracker.report_download_error(message)
    return None


def download_album_tracks(episode_dict, season_number: int, episode_index: int, scrape_serie) -> tuple:
    """Download one track of a YouTube Music album."""
    is_dict = isinstance(episode_dict, dict)
    name = (episode_dict.get("name") if is_dict else getattr(episode_dict, "name", None)) or "Unknown Track"

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
    entry.duration_seconds = (
        episode_dict.get("duration") if is_dict else getattr(episode_dict, "duration", None)
    )

    path = download_song(entry)
    if path:
        return (path, False, None)
    return (None, False, f"Download failed for '{name}'")