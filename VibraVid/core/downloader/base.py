# 2.03.26

import glob
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import NamedTuple

from rich.console import Console

from VibraVid.core.muxing import (
    build_hybrid_output,
    embed_poster,
    inject_chapters,
    join_media,
    probe_media_file,
)
from VibraVid.core.muxing.helper.audio import audio_ext_for_codec
from VibraVid.core.muxing.helper.video import get_media_metadata
from VibraVid.core.muxing.helper.video.hybrid import download_other_tracks
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.setup import get_ffmpeg_path
from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.hooks import execute_hooks
from VibraVid.utils.proc import run_logged
from VibraVid.utils.storage_upload.hook import is_cached, try_fetch, upload_after

console = Console()
logger = logging.getLogger(__name__)
LAST_DOWNLOADER_ERROR: str | None = None
EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
MERGE_SUBTITLES = config_manager.config.get_bool("PROCESS", "merge_subtitle")
MERGE_AUDIO = config_manager.config.get_bool("PROCESS", "merge_audio")
CLEANUP_TMP = config_manager.config.get_bool("DOWNLOAD", "cleanup_tmp_folder")
DEBUG_TRACK_JSON = config_manager.config.get_bool("DEFAULT", "debug_track_json")
DELAY_SS = config_manager.config.get_int("DOWNLOAD", "delay_after_download")
MUX_ENGINE = config_manager.config.get("PROCESS", "engine", default="ffmpeg")


_written_track_files: list[str] = []


class DownloadResult(NamedTuple):
    """Outcome of a downloader ``start()``."""
    path: str | None
    stopped: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None and not self.stopped and not self.error

    @classmethod
    def from_raw(cls, result: object) -> "DownloadResult":
        """Coerce a legacy return value (None, 2-tuple or 3-tuple) into a DownloadResult."""
        if isinstance(result, DownloadResult):
            return result
        if result is None:
            return cls(None, True, "no result")
        if isinstance(result, tuple):
            path = result[0] if len(result) >= 1 else None
            stopped = bool(result[1]) if len(result) >= 2 else True
            error = result[2] if len(result) >= 3 else None
            return cls(path, stopped, error)
        # A bare truthy value (e.g. a path string) -> treat as success.
        return cls(str(result), False, None)


def get_written_track_files() -> list[str]:
    """Paths of every TRACKS_JSON file written so far this run (see _append_tracks_json)."""
    return list(dict.fromkeys(_written_track_files))


def _append_tracks_json(payload: dict, service: str, media_type: str, title: str) -> None:
    """Write *payload* to a JSON file under .cache/logs, named ``<service>_<type>_<title>.json``."""
    try:
        log_dir = Path(config_manager.base_path or ".") / ".cache" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        parts = [service or "unknown", media_type or "Film", title or payload.get("name") or "output"]
        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", "_".join(p for p in parts if p))
        safe_name = re.sub(r"\s+", "_", safe_name).strip("_") or "output"

        track_file = log_dir / f"{safe_name}.json"

        entries = []
        if track_file.exists():
            try:
                entries = json.loads(track_file.read_text(encoding="utf-8"))
                if not isinstance(entries, list):
                    entries = []
            except Exception:
                entries = []

        entries.append(payload)
        track_file.write_text(json.dumps(entries, indent=4, ensure_ascii=False), encoding="utf-8")
        _written_track_files.append(str(track_file))
    except Exception as e:
        logger.error(f"Could not write TRACKS_JSON file: {e}")


class BaseDownloader:
    def __init__(self, output_path: str, temp_suffix: str, sanitize_path: bool = True, **kwargs):
        """Common initialisation shared by DASH / HLS / ISM sub-classes."""
        if not output_path:
            output_path = f"download.{EXTENSION_OUTPUT}"

        self.output_path = os_manager.get_sanitize_path(output_path) if sanitize_path else str(output_path)
        if not self.output_path.endswith(f".{EXTENSION_OUTPUT}"):
            self.output_path += f".{EXTENSION_OUTPUT}"

        # Media-token placeholders (quality/codec/language) can only be resolved AFTER muxing (see _finalize).
        self._final_name_template = self.output_path
        self.output_path = self._strip_media_tokens(self.output_path)

        self.filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        self.output_dir = self._resolve_temp_dir(temp_suffix)
        self.file_already_exists = self._finished_file_exists()

        self.download_id = context_tracker.download_id or str(uuid.uuid4())
        self.site_name = context_tracker.site_name

        self._error = None
        self.last_merge_result = None
        self.media_players = None
        self.copied_subtitles = []
        self.copied_audios = []
        self.audio_only = False

    def _resolve_temp_dir(self, temp_suffix: str) -> str:
        """Path of the scratch directory for segments, muxing leftovers and subtitles."""
        directory = os.path.dirname(self.output_path)
        temp_dir = os.path.join(directory, "." + self.filename_base + temp_suffix)

        # Adopt a temp dir left by a pre-dot-prefix version so its segments stay resumable.
        legacy_dir = os.path.join(directory, self.filename_base + temp_suffix)
        if os.path.isdir(legacy_dir) and not os.path.isdir(temp_dir):
            try:
                os.rename(legacy_dir, temp_dir)
                logger.info(f"Migrated legacy temp dir '{legacy_dir}' to '{temp_dir}'")
            except OSError as e:
                logger.warning(f"Could not migrate legacy temp dir '{legacy_dir}': {e}")
                return legacy_dir

        return temp_dir

    def _finished_file_exists(self) -> bool:
        """True if the final file already exists, INCLUDING media-token variants
        like '<name> [1080p].mkv' (the quality suffix is only added after muxing,
        so the plain stripped path may not match an already-downloaded episode)."""

        if os.path.exists(self.output_path):
            return True

        directory = os.path.dirname(self.output_path) or "."
        if not os.path.isdir(directory):
            return False

        pattern = os.path.join(directory, glob.escape(self.filename_base) + f"*.{EXTENSION_OUTPUT}")
        return any(os.path.isfile(p) for p in glob.glob(pattern))

    @property
    def error(self) -> str | None:
        return getattr(self, "_error", None)

    @error.setter
    def error(self, value: str | None) -> None:
        self._error = value
        global LAST_DOWNLOADER_ERROR
        try:
            LAST_DOWNLOADER_ERROR = str(value) if value is not None else None
        except Exception:
            LAST_DOWNLOADER_ERROR = None

    @staticmethod
    def _resolve_url(url_or_path: str) -> str:
        if not url_or_path:
            return url_or_path
        stripped = url_or_path.strip()

        if re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", stripped):
            return stripped

        path = Path(stripped)
        if path.exists() and path.is_file():
            return path.resolve().as_uri()
        return stripped

    def _build_other_tracks(self) -> list:
        """Return the normalized list of extra tracks attached to this download."""
        other_tracks = []
        for track in list(getattr(self, "other_tracks", []) or []):
            if not isinstance(track, dict):
                continue
            normalized_track = {
                "type": track.get("type"),
                "url": track.get("url"),
                "language": track.get("language"),
            }
            if normalized_track["type"] and normalized_track["url"]:
                other_tracks.append(normalized_track)
        return other_tracks

    def _format_keys(self, keys: list) -> list:
        """Return DRM keys formatted as ``kid:key`` strings."""
        formatted_keys = []
        for k in keys or []:
            if isinstance(k, (list, tuple)) and len(k) == 2:
                formatted_keys.append(f"{k[0]}:{k[1]}")
            else:
                formatted_keys.append(str(k))
        return formatted_keys

    def _build_download_cmd(self, name: str, manifest_url: str, formatted_keys: list) -> str:
        """Build a ready-to-run ``python manual.py --down`` command that re-downloads this video"""
        if not manifest_url:
            return ""

        safe_name = re.sub(r'[\\/:*?"<>|]+', "_", (name or "output")).strip() or "output"
        parts = [f"python manual.py --down '{manifest_url}'"]
        for k in formatted_keys:
            parts.append(f"--key {k}")

        parts.append(f"-o '{safe_name}'")
        return " ".join(parts)

    def track_download_start(self, title: str, media_type: str, site: str) -> None:
        """Fire-and-forget: notify Supabase that a download has started."""

        def _run():
            try:
                from VibraVid.utils.vault.vault_1 import claudio_vault

                if claudio_vault.is_connected:
                    title_str = (title or "").strip()
                    media_type_str = (media_type or "Film").strip()
                    site_str = (site or "").strip().lower()
                    logger.debug(f"[TRACK] Tracking download: title={title_str}, type={media_type_str}, service={site_str}")
                    result = claudio_vault.track_download(
                        title=title_str,
                        media_type=media_type_str,
                        service=site_str,
                    )
                    logger.debug(f"[TRACK] Track result: {result}")
            except Exception as e:
                logger.error(f"[TRACK] Error tracking download: {e}", exc_info=True)

        t = threading.Thread(target=_run, daemon=False)
        t.start()

    def _build_tracks_desc(self, streams: list) -> str:
        """Human-readable summary of tracks"""
        video_res: list[str] = []
        audio_langs: list[str] = []
        sub_langs: list[str] = []

        for stream in streams or []:
            stype = getattr(stream, "type", "")
            if stype == "video":
                if not getattr(stream, "selected", False):
                    continue
                res = getattr(stream, "resolution", "")
                codec = stream.get_short_codec() if hasattr(stream, "get_short_codec") else ""
                bitrate = ""
                if hasattr(stream, "bitrate_display"):
                    bitrate = stream.bitrate_display if stream.bitrate else ""
                elif hasattr(stream, "bandwidth"):
                    bw = stream.bandwidth
                    bitrate = "" if bw in ("0 bps", "N/A") else bw
                label = " ".join(p for p in (res, codec, bitrate) if p)
                if label and label not in video_res:
                    video_res.append(label)
            elif stype == "audio":
                lang = getattr(stream, "language", "")
                if lang and lang not in ("und", "") and lang not in audio_langs:
                    audio_langs.append(lang)
            elif stype == "subtitle":
                lang = getattr(stream, "language", "")
                if lang and lang not in ("und", "") and lang not in sub_langs:
                    sub_langs.append(lang)

        parts = []
        if video_res:
            parts.append("video: " + ", ".join(video_res))
        if audio_langs:
            parts.append("audio: " + ", ".join(audio_langs))
        if sub_langs:
            parts.append("subtitle: " + ", ".join(sub_langs))
        return " | ".join(parts)

    def _resolve_track_info(self) -> tuple[str, str, str, str]:
        """Emit a TRACKS_JSON logger.info and trigger Supabase download tracking."""
        season = context_tracker.season or 0
        episode = context_tracker.episode or 0
        episode_name = context_tracker.episode_name or ""
        media_type = context_tracker.media_type or "Film"
        if season > 0 or episode > 0:
            media_type = "TV"

        title = context_tracker.title or self.filename_base
        site = context_tracker.site_name or getattr(self, "site_name", "") or ""

        # Build the display name: "<title> S<season>_E<episode> <episode name>" for TV.
        name = title
        if media_type == "TV":
            name = f"{title} S{season}_E{episode}"
            if episode_name:
                name += f" {episode_name}"

        return title, media_type, site, name

    def _log_tracks_json(self, streams: list, keys: list, manifest_url: str) -> None:
        if not DEBUG_TRACK_JSON:
            return

        title, media_type, site, name = self._resolve_track_info()
        formatted_keys = self._format_keys(keys)
        payload = {
            "name": name,
            "manifest": manifest_url,
            "desc": self._build_tracks_desc(streams),
            "other_tracks": self._build_other_tracks(),
            "cmd": self._build_download_cmd(name, manifest_url, formatted_keys),
        }
        _append_tracks_json(payload, service=site, media_type=media_type, title=title)

    def _no_media_downloaded(self, status: dict) -> bool:
        """Return True when the download produced absolutely nothing."""
        logger.info(f"Download status: {status}")
        return (
            status.get("video") is None
            and not status.get("audios")
            and not status.get("subtitles")
            and not status.get("external_subtitles")
        )
    
    def _precheck(self, manifest_url: str) -> "DownloadResult | None":
        """Common short-circuits every protocol runs before its own pipeline.

        Returns a terminal :class:`DownloadResult` to hand straight back, or
        ``None`` to signal "keep going". Also creates the output directory.
        """
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return DownloadResult(self.output_path, False, None)

        if context_tracker.resolve_only:
            from VibraVid.cli.command.queue import enqueue_down_from_context

            enqueue_down_from_context(manifest_url, self.output_path)
            return DownloadResult(self.output_path, False, None)

        if is_cached():
            console.print("[dim]Skipping — already in cache.")
            return DownloadResult(self.output_path, False, None)

        if try_fetch(self.output_path):
            return DownloadResult(self.output_path, False, None)

        os_manager.create_path(self.output_dir)
        return None

    def _fail(self, error: str) -> "DownloadResult":
        """Record a failed terminal state on the tracker and return it."""
        if self.download_id:
            download_tracker.complete_download(self.download_id, success=False, error=error)
        return DownloadResult(None, True, error)

    def _check_download_status(self, status: dict) -> "DownloadResult | None":
        """Guard the raw download ``status``: return a terminal DownloadResult for
        a cancelled/empty run, or ``None`` when there is media to mux."""
        if status.get("error") == "cancelled":
            return self._fail("cancelled")
        if self._no_media_downloaded(status):
            logger.error("No media downloaded")
            return self._fail("No media downloaded")
        return None

    def _merge_and_finalize(self, status: dict) -> "DownloadResult":
        """Mux the downloaded tracks, move to the final path and return the result."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        final_file = self._merge_files(status)
        if not final_file:
            if self.download_id and download_tracker.is_stopped(self.download_id):
                return self._fail("cancelled")
            merge_error = self.error or "Merge failed"
            logger.error(merge_error)
            return self._fail(merge_error)

        self._finalize(final_file=final_file)
        if DELAY_SS > 0:
            console.print(f"\n[green]Sleeping {DELAY_SS} seconds before finishing...")
            time.sleep(DELAY_SS)

        return DownloadResult(self.output_path, False, self.error or None)

    def _maybe_enable_streaming_mux(self) -> None:
        """Called by HLS/DASH/ISM downloaders, after streams are selected but before start_download()"""
        service_module = sys.modules.get(f"VibraVid.services.{self.site_name}")
        if not getattr(service_module, "_live_mux", False) and not context_tracker.force_livemux:
            logger.info(f"streaming_mux: not enabled (service {self.site_name!r} does not opt in via _live_mux = True)")
            return

        if context_tracker.no_livemux:
            logger.info("streaming_mux: not enabled (--no-livemux)")
            return
        
        if MUX_ENGINE.lower() != "ffmpeg":
            logger.info(f"streaming_mux: not enabled (PROCESS.engine={MUX_ENGINE!r} != ffmpeg)")
            return
        
        if os.path.splitext(self.output_path)[1].lower() != ".mkv":
            logger.info(f"streaming_mux: not enabled (output_path={self.output_path!r} is not .mkv)")
            return

        media_downloader = getattr(self, "media_downloader", None)
        if media_downloader is None:
            logger.info("streaming_mux: not enabled (media_downloader not created yet)")
            return

        # Check media_downloader.other_tracks (the hybrid-only list, already filtered of
        # subtitle/extra-audio sidecar entries by the DASH/HLS/ISM downloader by this point)
        if getattr(media_downloader, "other_tracks", None):
            logger.info("streaming_mux: not enabled (other_tracks/hybrid output present)")
            return

        media_downloader.enable_streaming_mux(self.output_path)
        logger.info(f"streaming_mux: enabled for this download -> {self.output_path}")

    def _finish_from_status(self, status: dict) -> "DownloadResult":
        """Common tail for protocols with no post-download step: guards → mux."""
        return self._check_download_status(status) or self._merge_and_finalize(status)

    def _move_to_final_location(self, final_file: str) -> None:
        """
        Move *final_file* to ``self.output_path``.
        Updates ``self.output_path`` when the merge produced a different extension.
        """
        final_ext = os.path.splitext(final_file)[1].lower()
        desired_ext = os.path.splitext(self.output_path)[1].lower()
        if final_ext != desired_ext:
            base = os.path.splitext(self.output_path)[0]
            self.output_path = base + final_ext

        if os.path.abspath(final_file) != os.path.abspath(self.output_path):
            try:
                if os.path.exists(self.output_path):
                    os.remove(self.output_path)
                os.rename(final_file, self.output_path)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not move file: {e}")
                self.output_path = final_file

    def _decrypt_failure_message(self) -> str | None:
        """"Decryption failed - track(s) still encrypted: ..." from  media_downloader.decrypt_failures, or None if every track decrypted OK."""
        md = getattr(self, "media_downloader", None)
        failures = [f for f in (getattr(md, "decrypt_failures", []) or []) if not f.get("skipped")]
        if not failures:
            return None

        labels = ", ".join(dict.fromkeys(f.get("label") or f.get("track") or "?" for f in failures))
        return f"Decryption failed - track(s) still encrypted: {labels}"

    def _merge_files(self, status: dict) -> str | None:
        """
        Merge downloaded files using FFmpeg.
        Returns the resulting file path, or None on failure.
        """
        err = self._decrypt_failure_message()
        if err:
            logger.warning(f"{err} — skipping mux, the merged file would be rejected anyway")
            console.print(f"[yellow]{err} — skipping mux.")
            self.error = err
            return None

        streaming_mux_result = getattr(getattr(self, "media_downloader", None), "streaming_mux_result", None)
        if streaming_mux_result and os.path.exists(streaming_mux_result) and os.path.getsize(streaming_mux_result) > 0:
            logger.info(f"Using streaming-mux fast-path output, skipping join_media(): {streaming_mux_result}")
            merged_file = streaming_mux_result
            if not self._merge_output_ok(merged_file):
                return None
            
            if self.chapters:
                merged_file = self._inject_chapters(merged_file)

            return self._embed_poster(merged_file)

        video_track = status.get("video")
        audio_tracks: list[dict] = list(status.get("audios") or [])
        subtitle_tracks: list[dict] = [
            t for t in (status.get("subtitles") or []) if os.path.exists(t.get("path", "")) and os.path.getsize(t["path"]) > 0
        ]
        _dropped_empty_subs = len(status.get("subtitles") or []) - len(subtitle_tracks)
        if _dropped_empty_subs:
            logger.warning(f"Dropped {_dropped_empty_subs} empty subtitle track(s) before merge (0 bytes -- ffmpeg would reject them as input)")

        # DASH-specific external tracks
        for ext_audio in status.get("external_audios") or []:
            path = ext_audio.get("path", "")
            if path and os.path.exists(path):
                audio_tracks.append(
                    {
                        "path": path,
                        "name": ext_audio.get("language") or ext_audio.get("file", ""),
                        "language": ext_audio.get("language") or ext_audio.get("file", ""),
                        "size": os.path.getsize(path),
                    }
                )

        for ext_sub in status.get("external_subtitles") or []:
            path = ext_sub.get("path", "")
            if path and os.path.exists(path):
                subtitle_tracks.append(
                    {
                        "path": path,
                        "name": ext_sub.get("language") or ext_sub.get("file", ""),
                        "language": ext_sub.get("language") or ext_sub.get("file", ""),
                        "size": os.path.getsize(path),
                    }
                )

        other_track_specs = list(status.get("other_tracks") or [])
        other_track_results = list(status.get("other_tracks_downloaded") or [])

        if other_track_specs and not other_track_results:
            if self.download_id:
                download_tracker.update_status(self.download_id, "Downloading other tracks ...")

            other_track_results = download_other_tracks(
                other_track_specs,
                Path(self.output_dir),
                self.filename_base,
                keys=getattr(getattr(self, "media_downloader", None), "key", None),
                headers=getattr(self, "headers", None) or getattr(self, "mpd_headers", None) or {},
                cookies=getattr(self, "cookies", None) or {},
                max_segments=getattr(self, "max_segments", None),
                max_time=getattr(self, "max_time", None),
            )
            status["other_tracks_downloaded"] = other_track_results

        if other_track_specs and self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        for track in other_track_results:
            track_kind = (track.get("kind") or track.get("type") or "").lower()
            base_kind = track_kind.split(":")[0]  # "video:hdr10" -> "video"
            if base_kind == "video":
                continue
            if base_kind == "audio":
                audio_tracks.append(track)
            elif base_kind == "subtitle":
                subtitle_tracks.append(track)

        if video_track is None:
            if audio_tracks or subtitle_tracks:
                self.audio_only = True
                if audio_tracks:
                    self._track_audios_for_copy(audio_tracks)
                if subtitle_tracks:
                    self._track_subtitles_for_copy(subtitle_tracks)
                return self.output_path
            return None

        video_path = video_track["path"] if isinstance(video_track, dict) else video_track.get("path")

        if not os.path.exists(video_path):
            console.print(f"[red]Video file not found: {video_path}")

        video_probe = video_track.get("probe") if isinstance(video_track, dict) else None
        if not video_probe and video_path and os.path.exists(video_path):
            video_probe = probe_media_file(video_path)

        if other_track_results or (video_probe or {}).get("dolby_vision"):
            hybrid_file = build_hybrid_output(
                video_track=video_track if isinstance(video_track, dict) else {"path": video_path},
                other_videos=[
                    track
                    for track in other_track_results
                    if (track.get("kind") or track.get("type") or "").lower().split(":")[0] == "video"
                ],
                audio_tracks=audio_tracks,
                subtitle_tracks=subtitle_tracks,
                output_path=self.output_path,
                filename_base=self.filename_base,
            )
            if hybrid_file:
                self.last_merge_result = {"hybrid": True, "output": hybrid_file}
                return self._embed_poster(self._inject_chapters(hybrid_file))

        audio_tracks_to_merge: list = []
        if audio_tracks:
            if MERGE_AUDIO:
                audio_tracks_to_merge = audio_tracks
            else:
                self._track_audios_for_copy(audio_tracks)

        # CC-embedded subtitles are extracted straight from the raw video stream
        subtitle_tracks = self._prepare_subtitle_tracks_for_merge(subtitle_tracks, video_path)
        subtitle_tracks_to_merge: list = []
        if subtitle_tracks:
            if MERGE_SUBTITLES:
                subtitle_tracks_to_merge = subtitle_tracks
            else:
                self._track_subtitles_for_copy(subtitle_tracks)

        merged_file, result_json = join_media(
            video_path=video_path,
            audio_tracks=audio_tracks_to_merge,
            subtitle_tracks=subtitle_tracks_to_merge,
            out_path=self.output_path,
            chapters=getattr(self, "chapters", None),
            force_ts_fix=getattr(getattr(self, "media_downloader", None), "_needs_join_ts_fix", False),
        )
        self.last_merge_result = result_json
        if not self._merge_output_ok(merged_file):
            return None
        # Chapters are already baked into merged_file by join_media() above — no separate _inject_chapters() pass needed.
        return self._embed_poster(merged_file)

    def _inject_chapters(self, file_path: str) -> str:
        """Add this downloader's queued chapters (self.chapters) as the final muxing step."""
        merged_file, result_json = inject_chapters(file_path, getattr(self, "chapters", None), temp_dir=self.output_dir)
        if result_json:
            self.last_merge_result = result_json
        return merged_file

    def _embed_poster(self, file_path: str) -> str:
        """Embed this downloader's poster/still image (self.poster_url) as the final muxing step."""
        from VibraVid.services._base.tmdb_artwork import embed_enabled

        if not embed_enabled():
            return file_path

        merged_file, result_json = embed_poster(file_path, getattr(self, "poster_url", None))
        if result_json:
            self.last_merge_result = result_json
        return merged_file

    @staticmethod
    def _merge_output_ok(path: str) -> bool:
        """A merge step only succeeded if ffmpeg produced a non-empty file"""
        return bool(path) and os.path.exists(path) and os.path.getsize(path) > 0

    def _prepare_subtitle_tracks_for_merge(self, subtitle_tracks: list, video_path: str | None = None) -> list:
        """Hook for subclasses to materialize subtitle assets right before muxing (e.g. embedded-CC extraction from the raw video stream)."""
        return subtitle_tracks

    def _track_subtitles_for_copy(self, subtitles_list: list) -> None:
        """Stage subtitle files for deferred copy to final location."""
        for idx, subtitle in enumerate(subtitles_list):
            sub_path = subtitle.get("path")
            if sub_path and os.path.exists(sub_path):
                self.copied_subtitles.append(
                    {
                        "src": sub_path,
                        "language": subtitle.get("language", f"sub{idx}"),
                        "extension": os.path.splitext(sub_path)[1],
                    }
                )

    def _track_audios_for_copy(self, audios_list: list) -> None:
        """Stage audio files for deferred copy to final location."""
        for idx, audio in enumerate(audios_list):
            audio_path = audio.get("path")
            if audio_path and os.path.exists(audio_path):
                self.copied_audios.append(
                    {
                        "src": audio_path,
                        "language": audio.get("language", audio.get("name", f"audio{idx}")),
                        "extension": os.path.splitext(audio_path)[1],
                    }
                )

    def _move_copied_subtitles(self) -> None:
        """Move staged subtitle files to final location."""
        if not self.copied_subtitles:
            return

        output_dir = os.path.dirname(self.output_path)
        filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        for sub_info in self.copied_subtitles:
            dst = os.path.join(
                output_dir,
                f"{filename_base}.{sub_info['language']}{sub_info['extension']}",
            )

            try:
                shutil.copy2(sub_info["src"], dst)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not copy subtitle {sub_info['language']}: {e}")

    def _move_copied_audios(self) -> None:
        """Move staged audio files to final location."""
        if not self.copied_audios:
            return

        output_dir = os.path.dirname(self.output_path)
        filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        for idx, audio_info in enumerate(self.copied_audios):
            if self.audio_only and idx == 0:
                dst = self._audio_only_destination(audio_info["src"])
                self.output_path = dst
                move_func = shutil.move
            else:
                dst = os.path.join(
                    output_dir,
                    f"{filename_base}.{audio_info['language']}{audio_info['extension']}",
                )
                move_func = shutil.copy2

            try:
                if move_func is shutil.move and dst != audio_info["src"] and self._remux_needed(audio_info["src"], dst):
                    self._remux_audio(audio_info["src"], dst)
                else:
                    move_func(audio_info["src"], dst)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not move audio {audio_info['language']}: {e}")

    def _audio_only_destination(self, src: str) -> str:
        """Determine the destination path for an audio-only download, using codec-based extension if possible."""
        base_no_ext = os.path.splitext(self.output_path)[0]
        try:
            codec = (get_media_metadata(src) or {}).get("audio_codec", "")
        except Exception:
            codec = ""
        ext = audio_ext_for_codec(codec) or os.path.splitext(src)[1].lstrip(".") or "mka"
        return f"{base_no_ext}.{ext}"

    @staticmethod
    def _remux_needed(src: str, dst: str) -> bool:
        """True if the source and destination extensions differ, indicating that remuxing is needed to change container format."""
        return os.path.splitext(src)[1].lower() != os.path.splitext(dst)[1].lower()

    def _remux_audio(self, src: str, dst: str) -> None:
        """Remux audio to change container without re-encoding, using FFmpeg. Falls back to raw move on failure."""
        try:
            proc = run_logged(
                [get_ffmpeg_path(), "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-c:a", "copy", dst],
                "Remux audio", log=logger,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )

            if proc.returncode == 0 and os.path.exists(dst):
                os.remove(src)
                return

            logger.warning(f"Audio remux failed ({proc.returncode}): {proc.stderr[-200:]}; falling back to raw move")
        except Exception as e:
            logger.warning(f"Audio remux error: {e}; falling back to raw move")
        shutil.move(src, os.path.splitext(dst)[0] + os.path.splitext(src)[1])

    def _verify_output(self) -> bool:
        """Authoritative decryption check."""
        global LAST_DOWNLOADER_ERROR
        try:
            err = self._decrypt_failure_message()
            if not err:
                return True

            md = getattr(self, "media_downloader", None)
            failures = [f for f in (getattr(md, "decrypt_failures", []) or []) if not f.get("skipped")]
            detail = failures[0].get("message", "") if failures else ""
            logger.error(f"Decryption verification FAILED for {os.path.basename(self.output_path or '')}: {err} ({detail})")

            LAST_DOWNLOADER_ERROR = err
            self.error = err
            return False
        except Exception as exc:
            logger.warning(f"Output verification skipped due to error: {exc}")
            return True

    _MEDIA_PLACEHOLDERS = (
        "%(quality)",
        "%(language)",
        "%(video_codec)",
        "%(audio_codec)",
        "%(audio_flags)",
        "%(sub_flags)",
    )

    @classmethod
    def _strip_media_tokens(cls, path: str) -> str:
        """Remove unresolved media-token placeholders from *path*"""
        root, ext = os.path.splitext(path)
        for ph in cls._MEDIA_PLACEHOLDERS:
            root = root.replace(f" [{ph}]", "").replace(f"[{ph}]", "")
            root = root.replace(f" ({ph})", "").replace(f"({ph})", "")
            root = root.replace(ph, "")
        root = root.replace("  ", " ").rstrip(" .")
        return root + ext

    def _finalize(self, *, final_file: str) -> None:
        """Common tail for start(): move to final location."""
        if final_file and os.path.exists(final_file):
            self._move_to_final_location(final_file)

        # The working file was downloaded/muxed under a clean name (media tokens stripped).
        template = getattr(self, "_final_name_template", self.output_path)
        if any(p in template for p in self._MEDIA_PLACEHOLDERS):
            try:
                metadata = get_media_metadata(self.output_path)
                logger.info(f"Metadata for dynamic rename: {metadata}")

                replacements = {
                    "quality": metadata.get("quality", ""),
                    "language": metadata.get("language", ""),
                    "video_codec": metadata.get("video_codec", ""),
                    "audio_codec": metadata.get("audio_codec", ""),
                    "audio_flags": metadata.get("audio_flags", ""),
                    "sub_flags": metadata.get("sub_flags", ""),
                }

                # Resolve placeholders on the template's name root
                new_root = os.path.splitext(template)[0]
                cur_ext = os.path.splitext(self.output_path)[1]

                for key, val in replacements.items():
                    placeholder = f"%({key})"
                    if val:
                        new_root = new_root.replace(placeholder, str(val))
                    else:
                        new_root = (
                            new_root.replace(f"[{placeholder}]", "")
                            .replace(f"({placeholder})", "")
                            .replace(placeholder, "")
                        )

                new_root = new_root.replace("  ", " ").rstrip(" .")
                new_path = new_root + cur_ext

                if new_path != self.output_path:
                    new_dir = os.path.dirname(new_path)
                    if not os.path.exists(new_dir):
                        os.makedirs(new_dir, exist_ok=True)

                    # os.replace (not os.rename) so re-downloading an existing
                    # episode overwrites it instead of raising on Windows.
                    os.replace(self.output_path, new_path)
                    self.output_path = new_path
                    logger.info(f"Dynamic rename applied: {self.output_path}")

            except Exception as e:
                console.print(f"[yellow]Warning: Dynamic rename failed: {e}")

        self._move_copied_subtitles()
        self._move_copied_audios()
        verified_ok = self._verify_output()

        if self.output_path and os.path.exists(self.output_path):
            title, media_type, site, _ = self._resolve_track_info()
            self.track_download_start(title=title, media_type=media_type, site=site)

        # Never publish a file that failed decryption, is missing segments, or is below 1080p.
        if verified_ok:
            base_name = os.path.basename(self.output_path or "")
            missing = getattr(getattr(self, "media_downloader", None), "missing_segments_count", 0)
            if missing:
                logger.warning(f"Skipping vault upload for {base_name}: {missing} segment(s) missing")
            else:
                height = get_media_metadata(self.output_path).get("height", 0)
                if height < 1080:
                    logger.warning(f"Skipping vault upload for {base_name}: resolution below 1080p ({height}p)")
                else:
                    upload_after(self.output_path)

        if self.download_id:
            download_tracker.complete_download(
                self.download_id,
                success=verified_ok,
                path=os.path.abspath(self.output_path),
                error=None if verified_ok else LAST_DOWNLOADER_ERROR,
            )

        if CLEANUP_TMP:
            shutil.rmtree(self.output_dir, ignore_errors=True)

        execute_hooks("post_run")
