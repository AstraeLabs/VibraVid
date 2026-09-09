# 09.09.26

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from rich.console import Console

from VibraVid.setup import get_yt_dlp_path
from VibraVid.core.ui.bar_manager import DownloadBarManager

logger = logging.getLogger(__name__)
console = Console()


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class YTDLPDownloader:
    """Wrapper per yt-dlp binario."""

    def __init__(
        self,
        url: str,
        output_dir: str,
        filename: str | None = None,
        headers: dict | None = None,
        cookies: dict | None = None,
        proxy: str | None = None,
        format_spec: str | None = None,
        subtitle_langs: list[str] | None = None,
        write_subs: bool = False,
        write_auto_subs: bool = False,
        list_formats: bool = False,
        interactive_format: bool = False,
        extract_audio: bool = False,
        audio_format: str | None = None,
        audio_quality: str | None = None,
        playlist_end: int | None = None,
        download_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.cookies = cookies
        self.proxy = proxy
        self.format_spec = format_spec or "bestvideo+bestaudio/best"
        self.subtitle_langs = subtitle_langs or []
        self.write_subs = write_subs
        self.write_auto_subs = write_auto_subs
        self.list_formats = list_formats
        self.interactive_format = interactive_format
        self.extract_audio = extract_audio
        self.audio_format = audio_format
        self.audio_quality = audio_quality
        self.playlist_end = playlist_end
        self.download_id = download_id
        self.extra_args = kwargs

    def start(self) -> tuple[Path | None, bool, str | None]:
        yt_dlp = get_yt_dlp_path()
        if not yt_dlp:
            return None, False, "yt-dlp binary not found. Run with --binary-update or --dep to check."

        self.output_dir.mkdir(parents=True, exist_ok=True)
        files_before = set(self._list_downloaded_files())

        # Check if file already exists
        if self.filename:
            existing_files = [
                file_path
                for file_path in files_before
                if file_path.stem == self.filename
            ]
            if existing_files:
                console.print(f"[yellow]File already exists: {existing_files[0]}[/yellow]")
                return existing_files[0], False, None

        cmd = [yt_dlp]
        if self.list_formats or self.interactive_format:
            cmd.extend(["--quiet", "--list-formats"])
            if self.format_spec and self.format_spec != "bestvideo+bestaudio/best":
                cmd.extend(["-f", self.format_spec])
        else:
            cmd.extend([
                "-o",
                str(self.output_dir / f"{self.filename}.%(ext)s") if self.filename else str(self.output_dir / "%(title)s.%(ext)s"),
                "-f",
                self.format_spec,
                "--no-warnings",
                "--progress",
                "--newline",
            ])

        if self.extract_audio:
            cmd.append("--extract-audio")
        if self.audio_format:
            cmd.extend(["--audio-format", self.audio_format])
        if self.audio_quality:
            cmd.extend(["--audio-quality", self.audio_quality])
        if self.playlist_end is not None:
            cmd.extend(["--playlist-end", str(self.playlist_end)])

        for k, v in self.headers.items():
            cmd.extend(["--add-header", f"{k}:{v}"])

        if self.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            cmd.extend(["--add-header", f"Cookie:{cookie_str}"])

        if self.proxy:
            cmd.extend(["--proxy", self.proxy])

        if self.write_subs and self.subtitle_langs:
            cmd.extend(["--write-subs", "--sub-langs", ",".join(self.subtitle_langs)])
        if self.write_auto_subs:
            cmd.extend(["--write-auto-subs", "--sub-langs", ",".join(self.subtitle_langs or ["all"])])

        env = os.environ.copy()
        deno = self.extra_args.pop("deno_path", None)
        if deno:
            env["PATH"] = os.pathsep.join([str(Path(deno).parent), env.get("PATH", "")])

        for k, v in self.extra_args.items():
            if v is True:
                cmd.append(f"--{k.replace('_', '-')}")
            elif v not in (False, None, ""):
                cmd.extend([f"--{k.replace('_', '-')}", str(v)])

        cmd.append(self.url)
        logger.info(f"Running yt-dlp: {' '.join(cmd)}")

        output_lines: list[str] = []
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )

            if not (self.list_formats or self.interactive_format):
                with DownloadBarManager(self.download_id) as bar_manager:
                    if process.stdout is not None:
                        for line in process.stdout:
                            output_lines.append(line.rstrip())
                            self._handle_progress_line(bar_manager, line)
                    process.wait(timeout=3600)
                    bar_manager.finish_all_tasks()
            else:
                if process.stdout is not None:
                    for line in process.stdout:
                        output_lines.append(line.rstrip())
                process.wait(timeout=3600)

            if process.returncode != 0:
                error = "\n".join(output_lines[-20:]).strip() or f"Exit code {process.returncode}"
                logger.error(f"yt-dlp failed: {error}")
                return None, False, error

            if self.list_formats or self.interactive_format:
                self._print_formats(output_lines)
                if self.interactive_format:
                    try:
                        console.print("[purple]Select format ID to download[/purple]", end="")
                        console.print("(blank = Best, q = exit): ", style="green", end="")
                        choice = input("").strip()
                    except EOFError:
                        choice = ""
                    if choice.lower() in ("q", "exit", "quit"):
                        return None, True, "Interactive format selection cancelled"
                    if choice:
                        self.format_spec = choice
                    self.list_formats = False
                    self.interactive_format = False
                    return self.start()
                return None, False, None

            downloaded = self._find_downloaded_file()
            if downloaded:
                if downloaded in files_before:
                    console.print(f"[yellow]File already exists: {downloaded}[/yellow]")
                    return downloaded, False, None
                logger.info(f"Download completed: {downloaded}")
                console.print(f"[green]Download complete: {downloaded}[/green]")
                return downloaded, False, None

            return None, False, "Download completed but output file not found"

        except subprocess.TimeoutExpired:
            process.kill()
            return None, True, "Download timed out"
        except KeyboardInterrupt:
            return None, True, "Cancelled by user"
        except Exception as exc:
            logger.exception(f"yt-dlp execution failed: {exc}")
            return None, False, str(exc)

    @staticmethod
    def _handle_progress_line(bar_manager: DownloadBarManager, line: str) -> None:
        """Translate yt-dlp's progress output into VibraVid progress metrics."""
        cleaned = _ANSI_ESCAPE.sub("", line)
        pct_match = re.search(r"(?P<pct>\d+(?:\.\d+)?)%", cleaned)
        if not pct_match:
            return

        after_pct = cleaned[pct_match.end():]
        total = ""
        speed = ""
        total_match = re.search(r"\s+of\s+(\S+)", after_pct)
        if not total_match:
            total_match = re.search(r"\s+(~\S+)", after_pct)
        speed_match = re.search(r"\s+at\s+(\S+)", after_pct)
        if total_match:
            total = total_match.group(1)
        if speed_match:
            speed = speed_match.group(1)

        bar_manager.handle_progress_line(
            {
                "task_key": "yt_dlp",
                "label": "yt-dlp",
                "pct": float(pct_match.group("pct")),
                "speed": speed,
                "size": total,
            }
        )

    def _print_formats(self, output_lines: list[str]) -> None:
        """Show a compact, project-style yt-dlp format list."""
        relevant: list[str] = []
        for line in output_lines:
            stripped = line.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if lowered.startswith(("[youtube]", "[info]", "[download]")):
                continue
            if "available formats" in lowered or "format code" in lowered:
                relevant.append(stripped)
                continue
            if "|" in stripped or re.match(r"^\d+\s+", stripped):
                relevant.append(stripped)

        if not relevant:
            for line in output_lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("["):
                    relevant.append(stripped)

        if not relevant:
            return

        console.print("[bold green]Available formats[/bold green]")
        for line in relevant[:80]:
            print(f"  {_ANSI_ESCAPE.sub('', line)}")

    def _list_downloaded_files(self) -> list[Path]:
        """Return completed media files while excluding sidecar files."""
        ignored_suffixes = {
            ".part", ".ytdl", ".temp", ".vtt", ".srt", ".ass",
            ".lrc", ".json", ".description",
        }
        return [
            file_path
            for file_path in self.output_dir.iterdir()
            if file_path.is_file() and file_path.suffix.lower() not in ignored_suffixes
        ]

    def _find_downloaded_file(self) -> Path | None:
        """Trova il file scaricato nella output_dir."""
        # yt-dlp usa l'estensione reale, cerchiamo il file più recente che matcha il filename
        if self.filename:
            candidates = [
                file_path
                for file_path in self._list_downloaded_files()
                if file_path.stem == self.filename
            ]
        else:
            candidates = self._list_downloaded_files()
        if not candidates:
            return None

        # Ritorna il più grande (completo)
        return max(candidates, key=lambda p: p.stat().st_size)