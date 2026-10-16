# 16.10.26

import logging
import os
import re
import shutil
import tempfile

from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn, TransferSpeedColumn

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base.output_path import movie_folder

console = Console()
logger = logging.getLogger(__name__)


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "video"


class YTDLPProgressHook:
    """Progress hook for yt-dlp that drives a Rich Progress bar."""

    def __init__(self, title: str, uploader: str):
        self.title = title
        self.uploader = uploader
        self.progress = None
        self.task_id = None
        self._total = None

    def __call__(self, d):
        if d["status"] == "downloading":
            if self.progress is None:
                self.progress = Progress(
                    "[bold cyan]{task.description}",
                    BarColumn(bar_width=40),
                    "[progress.percentage]{task.percentage:>3.0f}%",
                    "•",
                    DownloadColumn(),
                    "•",
                    TransferSpeedColumn(),
                    "•",
                    TimeRemainingColumn(),
                    console=console,
                    transient=True,
                )
                self.progress.start()
                self.task_id = self.progress.add_task(f"{self.uploader} - {self.title}", total=None)

            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)

            if total:
                self._total = total
                self.progress.update(self.task_id, total=total, completed=downloaded)
            elif self._total:
                self.progress.update(self.task_id, completed=downloaded)

        elif d["status"] == "finished":
            if self.progress and self.task_id is not None:
                if self._total:
                    self.progress.update(self.task_id, completed=self._total)
                self.progress.stop()
                self.progress = None
                self.task_id = None


def download_video(select_title) -> str | None:
    """Download a YouTube video via yt-dlp with progress bar."""
    title = getattr(select_title, "title", "") or getattr(select_title, "name", "")
    uploader = getattr(select_title, "artist", "") or getattr(select_title, "uploader", "")
    video_id = getattr(select_title, "id", "")
    url = getattr(select_title, "url", "") or f"https://www.youtube.com/watch?v={video_id}"

    if not video_id and "youtube.com" not in url and "youtu.be" not in url:
        logger.warning(f"[youtube] Invalid YouTube URL: {url}")
        return None

    console.print(f"[cyan]Downloading via yt-dlp: [yellow]{uploader} - {title}[/yellow]")

    import yt_dlp

    progress_hook = YTDLPProgressHook(title, uploader)

    with tempfile.TemporaryDirectory() as temp_dir:
        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": os.path.join(temp_dir, "%(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "merge_output_format": "mp4",
            "progress_hooks": [progress_hook],
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Referer": "https://www.youtube.com/",
                "Origin": "https://www.youtube.com",
            },
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],
                    "player_skip": ["webpage", "configs"],
                }
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            downloaded_files = [f for f in os.listdir(temp_dir) if f.endswith((".mp4", ".mkv", ".webm", ".mov"))]
            if not downloaded_files:
                logger.warning(f"[youtube] No video file downloaded for {uploader} - {title}")
                return None

            downloaded_file = os.path.join(temp_dir, downloaded_files[0])
            logger.info(f"[youtube] Downloaded to temp: {downloaded_file}")

            safe_title = _sanitize_filename(title)
            dest_base = os.path.join(movie_folder(), f"{safe_title}")
            dest_ext = os.path.splitext(downloaded_file)[1] or ".mp4"
            dest_path = f"{dest_base}{dest_ext}"

            if os.path.exists(dest_path):
                base, ext = os.path.splitext(dest_path)
                counter = 1
                while os.path.exists(f"{base} ({counter}){ext}"):
                    counter += 1
                dest_path = f"{base} ({counter}){ext}"

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.move(downloaded_file, dest_path)
            logger.info(f"[youtube] Moved to: {dest_path}")

            context_tracker.report_download_success()
            return dest_path

        except Exception as e:
            logger.exception(f"[youtube] Download crashed for {title!r}: {e}")
            console.print(f"[red]Download failed: {e}")
            return None
