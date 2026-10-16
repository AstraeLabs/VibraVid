# 16.10.26
# By @Syncs-Luca98

import logging

from rich.console import Console

from VibraVid.core.downloader import YTDLPDownloader
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import movie_folder, print_download_header
from VibraVid.services._base.tv_display_manager import map_movie_path
from VibraVid.setup import get_deno_path
from VibraVid.utils import start_message

console = Console()
logger = logging.getLogger(__name__)

VIDEO_FORMAT = "bestvideo+bestaudio/best"


def download_video(select_title) -> tuple:
    """Download a YouTube video with the managed yt-dlp binary; returns (path, stopped, error)."""
    title = getattr(select_title, "title", "") or getattr(select_title, "name", "")
    video_id = getattr(select_title, "id", "")
    url = getattr(select_title, "url", "") or f"https://www.youtube.com/watch?v={video_id}"

    if not video_id and "youtube.com" not in url and "youtu.be" not in url:
        error = f"Invalid YouTube URL: {url}"
        logger.warning(f"[youtube] {error}")
        context_tracker.report_download_error(error)
        return None, False, error

    start_message()
    print_download_header(select_title)

    path_components, filename = map_movie_path(title, getattr(select_title, "year", None) or None)
    downloader = YTDLPDownloader(
        url=url,
        output_dir=movie_folder(*path_components),
        filename=filename,
        format_spec=VIDEO_FORMAT,
        merge_output_format="mp4",
        no_playlist=True,
        deno_path=get_deno_path(),
    )
    path, stopped, error = downloader.start()

    if error:
        logger.error(f"[youtube] Download failed for {title!r}: {error}")
        context_tracker.report_download_error(error)
        return path, stopped, error

    logger.info(f"[youtube] Downloaded to: {path}")
    context_tracker.report_download_success()
    return path, stopped, None
