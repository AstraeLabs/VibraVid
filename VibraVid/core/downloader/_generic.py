# 09.06.26

import copy
import logging
import os
import threading
from types import SimpleNamespace
from typing import Any

from rich.console import Console

from VibraVid.core.decryptor.keys_manager import KeysManager
from VibraVid.core.muxing import probe_media_file
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.ui.tracker import context_tracker, download_tracker
from VibraVid.core.ui.ui import build_table
from VibraVid.core.utils.codec import DV_CODEC_PREFIXES
from VibraVid.core.utils.language import language_variants
from VibraVid.core.utils.selector import (
    FilterSpec,
    StreamSelector,
    StreamSelectorFormatter,
    _height,
    _matches_res,
    strip_dv_suffix,
)
from VibraVid.core.velora.downloader import SKIP_POST_DECRYPT, MediaDownloader
from VibraVid.core.velora.util._stream_helpers import join_interruptible
from VibraVid.core.velora.util.formatting import (
    parse_max_segments as _parse_max_segments,
)
from VibraVid.core.velora.util.formatting import (
    parse_max_time as _parse_max_time,
)
from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.http_client import create_client, get_headers

from .base import BaseDownloader, DownloadResult
from .mp4 import MP4_Downloader

console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
_MEDIA_TYPES = ("video", "audio", "subtitle")
_DIRECT_MEDIA_EXTS = (".mp4", ".m4v", ".m4a", ".mp3", ".aac", ".wav")


def _track_signature(s) -> tuple:
    """Signature used to drop cross-manifest duplicates (keep the first seen)."""
    codec = (getattr(s, "codecs", "") or "").strip().lower()
    btr = getattr(s, "bitrate", 0) or 0

    if s.type == "video":
        res = (getattr(s, "resolution", "") or "").lower() or f"{getattr(s, 'width', 0)}x{getattr(s, 'height', 0)}"
        return ("video", codec, res, btr)

    if s.type == "audio":
        lang = (getattr(s, "resolved_language", "") or getattr(s, "language", "") or "").lower()
        ch = (getattr(s, "channels", "") or "").lower()
        return ("audio", codec, lang, ch, btr)

    lang = (getattr(s, "resolved_language", "") or getattr(s, "language", "") or "").lower()
    return (
        "subtitle",
        lang,
        codec,
        bool(getattr(s, "forced", False)),
        bool(getattr(s, "is_cc", False)),
        bool(getattr(s, "is_sdh", False)),
    )


def _is_dv(s) -> bool:
    """True if the stream is a Dolby Vision video track."""
    if getattr(s, "type", "") != "video":
        return False

    if (getattr(s, "video_range", "") or "").upper() == "DV":
        return True

    codecs = (getattr(s, "codecs", "") or "").lower()
    return any(codecs.startswith(p) for p in DV_CODEC_PREFIXES)


def _normalize_lang(s) -> None:
    """Normalise language code"""
    if s.type not in ("audio", "subtitle"):
        return

    base = getattr(s, "resolved_language", "") or getattr(s, "language", "")
    if not base:
        return

    if s.type == "audio":
        s.language = base.split("-")[0].lower()
    else:
        s.language = base.lower()


class Generic_Downloader(BaseDownloader):
    _ROLE_KIND_TO_TYPE = {
        "video": "video",
        "vid": "video",
        "audio": "audio",
        "aud": "audio",
        "subtitle": "subtitle",
        "sub": "subtitle",
    }


    def __init__(
        self,
        sources: list[dict[str, Any]],
        output_path: str | None = None,
        max_segments: int | None = None,
        max_time=None,
        cookies: dict[str, str] | None = None,
        custom_filters: dict[str, str] | None = None,
        chapters: list | None = None,
        poster_url: str | None = None,
    ) -> None:
        """
        Parameters:
            - sources: list of source dicts (see class docstring).
            - output_path: final output file path. Default: "download.{ext}".
            - max_segments: cap downloaded segments per source (for testing).
            - max_time: cap downloaded duration, e.g. "01:00:00" or seconds.
            - cookies: default cookies applied to sources without their own.
            - custom_filters: optional {"video","audio","subtitle"} selector
              overrides; otherwise the values from config.json are used.
            - chapters: Chapter markers to inject into the muxed output, e.g. [{"name": str, "seconds": int}]. Default: context_tracker.chapters.
            - poster_url: Poster/still image URL to embed in the muxed output. Default: context_tracker.poster_url.
        """
        self.sources = [dict(s or {}) for s in (sources or [])]
        self.cookies = cookies or {}
        self.max_segments = _parse_max_segments(
            max_segments if max_segments is not None else context_tracker.max_segments
        )
        self.max_time = _parse_max_time(max_time if max_time is not None else context_tracker.max_time)
        self.custom_filters = custom_filters or {}
        self.chapters = chapters if chapters is not None else context_tracker.chapters
        self.poster_url = context_tracker.poster_url or poster_url or context_tracker.fallback_poster_url
        context_tracker.poster_url = self.poster_url
        context_tracker.poster_url = self.poster_url
        self._active: list[tuple[MediaDownloader, dict[str, Any]]] = []
        self._dv_stream = None
        self._dv_isolated = False
        self.other_tracks: list = []
        self._direct_sources: list[dict[str, Any]] = []
        logger.info(f"Initialized GENERIC_Downloader with {len(self.sources)} source(s), max_segments={self.max_segments}")
        super().__init__(output_path, "_generic_temp")

    def _fetch_manifest_content(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch raw manifest text (needed only when a source forces a protocol, because the type auto-detection keys off the URL extension)."""
        try:
            with create_client(headers=headers or get_headers(), timeout=20, follow_redirects=True) as c:
                resp = c.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            logger.error(f"Failed to pre-fetch manifest {url!r}: {exc}")
            return None

    @staticmethod
    def _is_direct_media_url(url: str) -> bool:
        """True if the URL points at a plain media file (not a manifest to parse) — e.g. a raw .mp4/.m4a."""
        return url.lower().split("?")[0].endswith(_DIRECT_MEDIA_EXTS)

    def _parse_sources(self) -> list[tuple[MediaDownloader, dict[str, Any]]]:
        """Parse every source into a MediaDownloader with its streams. The source dict is kept alongside for reference (e.g. headers, protocol)."""
        parsed: list[tuple[MediaDownloader, dict[str, Any]]] = []
        for i, source in enumerate(self.sources):
            label = source.get("label") or f"src{i}"
            url = self._resolve_url(str(source.get("url") or "").strip())
            if not url:
                console.print(f"[yellow]Source '{label}' has no url, skipping.")
                continue

            out_dir = os_manager.get_sanitize_path(f"{self.output_dir}/{label}")
            os_manager.create_path(out_dir)

            if not source.get("protocol") and self._is_direct_media_url(url):
                self._direct_sources.append({"label": label, "url": url, "out_dir": out_dir, "source": source})
                continue

            protocol = source.get("protocol")
            content = source.get("manifest_content")
            if not content and protocol:
                content = self._fetch_manifest_content(url, source.get("headers") or {})

            md = MediaDownloader(
                url=url,
                output_dir=out_dir,
                filename=self.filename_base,
                headers=source.get("headers") or {},
                cookies=source.get("cookies") or self.cookies,
                download_id=self.download_id,
                site_name=self.site_name,
                max_segments=self.max_segments,
                max_time=self.max_time,
                manifest_content=content,
                manifest_protocol=protocol,
            )

            md.parse_stream(show_table=False)
            for s in md.streams:
                s._src_label = label
                _normalize_lang(s)

            parsed.append((md, source))
        return parsed

    def _download_direct_sources(self) -> bool:
        """Download every self._direct_sources entry (plain .mp4/.m4a/... files) via Mp4_Downloader. Returns False if cancelled (Ctrl+C)."""
        for entry in self._direct_sources:
            source = entry["source"]
            label = entry["label"]
            url = entry["url"]

            role = str(source.get("role") or source.get("type") or "video").strip().lower()
            kind = role.split(":")[0]
            ext = os.path.splitext(url.split("?")[0])[1] or ".mp4"
            out_path = os.path.join(entry["out_dir"], f"{self.filename_base}{ext}")

            console.print(f"[cyan]Downloading direct source '[yellow]{label}[/yellow]' ({kind})...")
            path, need_stop, error = MP4_Downloader(
                url=url,
                path=out_path,
                headers=source.get("headers") or {},
                download_id=self.download_id,
                site_name=self.site_name,
                label=label,
                key=source.get("key"),
                check_content_type=True,
            )

            if need_stop:
                return False

            if not path or not os.path.exists(path):
                logger.error(f"Direct source '{label}' failed to download: {error}")
                console.print(f"[red]Direct source '{label}' failed: {error}")
                continue

            entry["result"] = {
                "path": path,
                "kind": kind,
                "language": source.get("language") or source.get("lang") or "und",
                "size": os.path.getsize(path),
            }

        return True
    
    def _apply_explicit_roles(
        self, parsed: list[tuple[MediaDownloader, dict[str, Any]]], video_filter: str = ""
    ) -> tuple[list, list[tuple[MediaDownloader, dict[str, Any]]]]:
        """Apply per-source explicit ``role`` tags and split them off from auto-selection.

            {"url": ..., "key": ..., "role": "video:dv"}   # Dolby Vision video
            {"url": ..., "key": ..., "role": "video:hdr10"}
            {"url": ..., "key": ..., "role": "audio", "language": "en"}
            {"url": ..., "key": ..., "role": "subtitle", "language": "en"}

        Returns ``(role_streams, auto_parsed)`` where ``auto_parsed`` are the sources left to the normal pool/dedup/StreamSelector path.
        """
        role_streams: list = []
        auto_parsed: list[tuple[MediaDownloader, dict[str, Any]]] = []
        vf_stripped, _ = strip_dv_suffix(video_filter or "")
        video_res = FilterSpec.parse(vf_stripped, "video").res if vf_stripped else None

        for md, src in parsed:
            role = str(src.get("role") or src.get("type") or "").strip().lower()
            if not role:
                auto_parsed.append((md, src))
                continue

            kind, _, tag = role.partition(":")
            kind, tag = kind.strip(), tag.strip()

            cands = [s for s in md.streams if not getattr(s, "is_external", False)]
            if not cands:
                logger.warning(f"Source role '{role}' has no parsable stream — skipping")
                continue

            # A role manifest often contains OTHER stream types too
            expected_type = self._ROLE_KIND_TO_TYPE.get(kind)
            if expected_type:
                typed_cands = [s for s in cands if getattr(s, "type", "") == expected_type]
                if typed_cands:
                    cands = typed_cands
                else:
                    logger.warning(f"Source role '{role}': manifest has no {expected_type} stream, using full pool as fallback")

            # "video:dv" is a compatibility companion track
            if expected_type == "video" and tag == "dv":
                stream = min(cands, key=lambda s: getattr(s, "bitrate", 0) or 0)
            else:
                # Other video roles (e.g. "video:hdr10", the base/primary track)
                if expected_type == "video" and video_res:
                    res_cands = [s for s in cands if _matches_res(s, video_res)]
                    if res_cands:
                        cands = res_cands
                    else:
                        logger.info(f"Source role '{role}': no stream matches res={video_res}, using full pool")

                stream = max(cands, key=lambda s: getattr(s, "bitrate", 0) or 0)
            for s in md.streams:
                s.selected = s is stream

            lang = src.get("language") or src.get("lang")
            name = src.get("name")

            if kind in ("video", "vid"):
                stream.type = "video"
                if tag == "dv":
                    stream.video_range = "DV"
                    stream.resolution = stream.resolution or "DV"
                    self._dv_stream = stream
                    self._dv_isolated = True
                elif tag:
                    stream.video_range = tag.upper()  # HDR10, HDR10PLUS, SDR, ...

            elif kind in ("audio", "aud"):
                stream.type = "audio"
                if lang:
                    stream.language = lang

            elif kind in ("subtitle", "sub"):
                stream.type = "subtitle"
                if lang:
                    stream.language = lang

                # Also read the source's "tag" field (e.g. "forced")
                src_tag = (src.get("tag") or tag or "").strip().lower()
                if src_tag == "forced":
                    stream.forced = True
                elif src_tag:
                    logger.debug(f"Subtitle tag '{src_tag}' not recognized — ignoring")

            else:
                logger.warning(f"Unknown source role '{role}' — treating as video")
                stream.type = "video"

            if name:
                stream.name = name
            stream._src_label = src.get("label") or kind
            _normalize_lang(stream)

            role_streams.append(stream)
            logger.info(f"Explicit role '{role}' -> {stream.type} (range={getattr(stream, 'video_range', '')!r}, lang={getattr(stream, 'language', '')!r})")

        return role_streams, auto_parsed

    def _select(self, parsed: list[tuple[MediaDownloader, dict[str, Any]]]) -> list:
        f = self.custom_filters
        v = f.get("video") or config_manager.config.get("DOWNLOAD", "select_video")
        a = f.get("audio") or config_manager.config.get("DOWNLOAD", "select_audio")
        sub = f.get("subtitle") or config_manager.config.get("DOWNLOAD", "select_subtitle")

        # Sources with an explicit role bypass attribute-based dedup/selection.
        role_streams, parsed = self._apply_explicit_roles(parsed, v)

        # Merge + dedup (keep first occurrence in source order).
        pool: list = []
        seen: set = set()
        for md, _ in parsed:
            for s in md.streams:
                if getattr(s, "is_external", False):
                    continue
                sig = _track_signature(s)
                if sig in seen:
                    continue
                seen.add(sig)
                pool.append(s)

        # Reset selection across ALL sources, then select once over the pool.
        for md, _ in parsed:
            for s in md.streams:
                s.selected = False

        # Check for the &dv companion tag in the video filter. If present, we run a first pass of selection on the non-DV pool with the main video filter.
        v_main, dv_quality = strip_dv_suffix(v)
        if dv_quality is not None:
            v_main = v_main or "best"
            non_dv_pool = [s for s in pool if not _is_dv(s)]
            selector = self._build_selector(v_main, a, sub)
            selector.apply(non_dv_pool)

            dv_videos = [s for s in pool if _is_dv(s)]
            if dv_videos:
                primary_video = next((s for s in non_dv_pool if getattr(s, "type", "") == "video" and s.selected), None)
                if primary_video is not None:
                    target_res = str(_height(primary_video)) if _height(primary_video) else None
                else:
                    target_res = FilterSpec.parse(v_main, "video").res
                selector._mark_dv_companion(dv_videos, dv_quality, target_res)
        else:
            self._build_selector(v, a, sub).apply(pool)

        # If a DV companion was selected, keep a reference to it for special handling in the download and muxing phases.
        # An explicit-role DV (self._dv_stream already set) takes precedence over &dv auto-detection.
        if self._dv_stream is None:
            self._dv_stream = next((s for s in pool if getattr(s, "dv_companion", False)), None)
            if self._dv_stream is not None:
                self._dv_stream.selected = True
                logger.info(f"&dv: companion selected -> {self._dv_stream}")

        return role_streams + [s for s in pool if s.selected]

    def _build_selector(self, video: str, audio: str, subtitle: str) -> StreamSelector:
        f = self.custom_filters
        return StreamSelector(
            video,
            audio,
            subtitle,
            formatter=StreamSelectorFormatter(),
            prefer_h265=bool(f.get("prefer_h265")),
            prefer_hdr10=bool(f.get("prefer_hdr10")),
            prefer_drm=bool(f.get("prefer_drm")),
            require_drm=bool(f.get("require_drm")),
            minimum_video_height=int(f.get("minimum_video_height") or 0),
        )

    def _setup_dv_companion(self) -> None:
        """Re download the manifest of the DV companion in a dedicated MediaDownloader, to isolate it from the main video stream and avoid filename collisions on disk (both have the same "{filename}.{ext}")."""
        if self._dv_stream is None:
            return

        # An explicit-role DV source already lives in its own MediaDownloader
        # (own out_dir), so there is no filename collision and no re-parse needed.
        if self._dv_isolated:
            logger.info("&dv: DV companion came from an explicit role source — already isolated")
            return

        owner = next(((md, src) for md, src in self._active if self._dv_stream in md.streams), None)
        if owner is None:
            self._dv_stream = None
            return

        md, source = owner
        self._dv_stream.selected = False
        target = copy.copy(self._dv_stream)
        target.selected = True

        dv_dir = os_manager.get_sanitize_path(f"{self.output_dir}/_dv")
        os_manager.create_path(dv_dir)

        dv_md = MediaDownloader(
            url=md.url,
            output_dir=dv_dir,
            filename=self.filename_base,
            headers=source.get("headers") or {},
            cookies=source.get("cookies") or self.cookies,
            download_id=self.download_id,
            site_name=self.site_name,
            max_segments=self.max_segments,
            max_time=self.max_time,
        )
        dv_md.manifest_type = md.manifest_type
        dv_md.streams = [target]

        target._src_label = "dv"
        self._dv_stream = target
        self._active.append((dv_md, source))
        logger.info(f"&dv: companion isolated in dedicated downloader -> {target}")

    def _stop_all(self) -> None:
        if self.download_id:
            download_tracker.request_stop(self.download_id)
        for md, _ in self._active:
            md._stop_event.set()
            md._cancel_all_loops()

    def _run_downloads(self) -> bool:
        """Download every selected stream of every source concurrently on ONE shared progress bar. Returns False if cancelled (Ctrl+C)."""
        for md, source in self._active:
            md.set_key(source.get("key"))
            sel = [s for s in md.streams if s.selected and not s.is_external and s.type in _MEDIA_TYPES]

            # Warn early if a source has encrypted tracks but no way to decrypt them.
            # Without this the file silently merges still-encrypted (the audio-without-key case).
            def _is_encrypted(s) -> bool:
                drm = getattr(s, "drm", None)
                try:
                    return bool(drm and drm.is_encrypted())
                except Exception:
                    return False

            encrypted_sel = [s for s in sel if _is_encrypted(s)]
            label = source.get("label") or (str(source.get("url") or "?")[:60])

            if encrypted_sel and not source.get("key") and not source.get("license_url"):
                kinds = ", ".join(sorted({s.type for s in encrypted_sel}))
                console.print(
                    f"[bold red][!] WARNING[/bold red] Source '[yellow]{label}[/yellow]': "
                    f"{len(encrypted_sel)} encrypted track(s) ([cyan]{kinds}[/cyan]) but no "
                    f"[bold]key[/bold]/[bold]license_url[/bold] provided - these tracks will stay encrypted."
                )
                logger.error(f"Generic source '{label}': encrypted {kinds} stream(s) without key/license — will remain encrypted")

            # Warn early per-track when keys ARE provided but none of them match this specific track's KID
            elif encrypted_sel and source.get("key") and not source.get("license_url"):
                provided_kids = {kid.lower() for kid, _ in KeysManager.normalize(source.get("key"))}
                for s in encrypted_sel:
                    track_kids = {k.lower() for k in (s.drm.get_all_kids() if s.drm else [])}
                    if track_kids and provided_kids.isdisjoint(track_kids):
                        track_label = f"{s.type} {s.resolution or s.language or ''}".strip()
                        console.print(f"[bold red][!] WARNING[/bold red] Source '[yellow]{label}[/yellow]': track [yellow]{track_label}[/yellow] needs KID(s) [magenta]{', '.join(track_kids)}[/magenta] ")
                        logger.error(f"Generic source '{label}': track {track_label} KID(s) {track_kids} not covered by provided keys")

            md._session_live_decrypt = (
                bool(sel)
                and not SKIP_POST_DECRYPT
                and all(getattr(s, "supports_live_decryption", False) for s in sel)
            )
            md._prepare_labels()

        def _safe_download(md, stream, bm) -> None:
            try:
                md._download_stream(stream, bm)
            except Exception as exc:
                logger.error(f"Stream download error ({stream.type}/{getattr(stream, 'language', '')}): {exc}", exc_info=True)

        bar = DownloadBarManager(self.download_id)
        stop_event = threading.Event()
        threads: list[threading.Thread] = []
        try:
            with bar as bm:
                for md, _ in self._active:
                    bm.add_prebuilt_tasks(md._get_prebuilt_tasks())

                for md, _ in self._active:
                    for s in md.streams:
                        if s.selected and not s.is_external and s.type in _MEDIA_TYPES:
                            t = threading.Thread(target=_safe_download, args=(md, s, bm), daemon=True)
                            threads.append(t)
                            t.start()

                join_interruptible(threads, stop_event)
                bm.finish_all_tasks()

        except KeyboardInterrupt:
            logger.warning("KeyboardInterrupt — stopping all hybrid sources")
            self._stop_all()
            stop_event.set()
            join_interruptible(threads, threading.Event(), hard_timeout=15.0)
            return False

        if any(md._stop_check() for md, _ in self._active):
            return False
        return True

    def _dv_entry(self, video_track: dict[str, Any]) -> dict[str, Any]:
        """Wrap the downloaded Dolby Vision video file as an 'other video' track consumable by ``build_hybrid_output`` (mkvmerge)."""
        path = video_track["path"]
        probe = probe_media_file(path) or {}
        entry = {
            "path": path,
            "url": "",
            "type": "video:dv",
            "kind": "video",
            "tag": "dv",
            "language": "und",
            "name": "Dolby Vision",
            "size": video_track.get("size", 0),
            "probe": probe,
        }
        entry.update(probe)
        return entry

    def _collect_status(self) -> dict[str, Any]:
        """Assemble a combined status dict (video/audios/subtitles) for muxing."""
        status: dict[str, Any] = {
            "video": None,
            "audios": [],
            "subtitles": [],
            "external_audios": [],
            "external_subtitles": [],
            "other_tracks": [],
            "other_tracks_downloaded": [],
        }

        for md, _ in self._active:
            md_status = md._build_status([], [])

            sel_audio_langs = [
                (getattr(s, "resolved_language", "") or getattr(s, "language", "") or "und")
                for s in md.streams
                if s.selected and s.type == "audio"
            ]

            md_video = md_status.get("video")
            if md_video:
                is_dv = self._dv_stream is not None and self._dv_stream in md.streams

                if is_dv:
                    status["other_tracks_downloaded"].append(self._dv_entry(md_video))
                elif status["video"] is None:
                    status["video"] = md_video

            for i, a in enumerate(md_status.get("audios", []) or []):
                lang = sel_audio_langs[i] if i < len(sel_audio_langs) else (a.get("name") or "und")
                status["audios"].append(
                    {**a, "name": a.get("name") or lang, "language": lang, **language_variants(lang)}
                )

            for sub in md_status.get("subtitles", []) or []:
                if sub.get("path"):
                    status["subtitles"].append(sub)

        for entry in self._direct_sources:
            result = entry.get("result")
            if not result:
                continue

            kind = result["kind"]
            lang = result["language"]

            if kind in ("video", "vid"):
                if status["video"] is None:
                    status["video"] = {"path": result["path"], "size": result["size"]}
            elif kind in ("audio", "aud"):
                status["audios"].append(
                    {
                        "path": result["path"],
                        "name": lang,
                        "language": lang,
                        "size": result["size"],
                        **language_variants(lang),
                    }
                )
            elif kind in ("subtitle", "sub"):
                status["subtitles"].append(
                    {
                        "path": result["path"],
                        "name": lang,
                        "language": lang,
                        "size": result["size"],
                    }
                )
            else:
                logger.warning(f"Direct source '{entry['label']}' has unknown kind '{kind}' — treating as video")
                if status["video"] is None:
                    status["video"] = {"path": result["path"], "size": result["size"]}

        return status

    def start(self) -> DownloadResult:
        try:
            return self._start()
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupt received — stopping all sources...")
            logger.warning("KeyboardInterrupt during hybrid pipeline")
            self._stop_all()
            return self._fail("cancelled")

    def _start(self) -> DownloadResult:
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return DownloadResult(self.output_path, False, None)

        os_manager.create_path(self.output_dir)

        if self.chapters:
            logger.info(f"Adding {len(self.chapters)} external chapter(s).")

        # ── 1) Parse every source (manifest-based sources here; plain .mp4/.m4a/... sources are split off into self._direct_sources instead)
        parsed = self._parse_sources()
        if not parsed and not self._direct_sources:
            return DownloadResult(None, True, "no sources parsed")

        # ── 2-3) Merge + dedup + single selection (only meaningful for manifest sources)
        selected = self._select(parsed) if parsed else []

        all_streams = [s for md, _ in parsed for s in md.streams if not s.is_external]
        if all_streams:
            console.print(build_table(all_streams))

        if not selected and not self._direct_sources:
            console.print("[yellow][HYBRID] No track selected.")
            return DownloadResult(None, True, "no tracks selected")

        self._active = [
            (md, src)
            for md, src in parsed
            if any(s.selected and not s.is_external and s.type in _MEDIA_TYPES for s in md.streams)
        ]
        self._setup_dv_companion()
        self._active = [
            (md, src)
            for md, src in self._active
            if any(s.selected and not s.is_external and s.type in _MEDIA_TYPES for s in md.streams)
        ]

        # ── 4) Concurrent download (clean Ctrl+C)
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        if self._direct_sources and not self._download_direct_sources():
            return self._fail("cancelled")

        if not self._run_downloads():
            return self._fail("cancelled")

        # ── 5) Mux
        status = self._collect_status()
        if self._no_media_downloaded(status):
            logger.error("No media downloaded")
            return self._fail("No media downloaded")

        if self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        # `_merge_files` (inherited from `BaseDownloader`) refuses to mux a track that never actually decrypted by checking `self.media_downloader.decrypt_failures`
        self.media_downloader = SimpleNamespace(
            decrypt_failures=[f for md, _src in self._active for f in getattr(md, "decrypt_failures", None) or []]
        )

        final_file = self._merge_files(status)
        if not final_file:
            return self._fail(self.error or "Merge failed")

        self._finalize(final_file=final_file)
        return DownloadResult(self.output_path, False, self.error or None)
