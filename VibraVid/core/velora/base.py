# 10.04.26

import logging
import platform
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rich.console import Console

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.manifest.custom import CustomParser, is_custom_manifest
from VibraVid.core.manifest.ism import ISMParser
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import Stream
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.ui.ui import build_table
from VibraVid.core.utils.codec import AUDIO_EXTENSIONS, SUBTITLE_CODEC_MAP, SUBTITLE_EXTENSIONS, VIDEO_EXTENSIONS
from VibraVid.core.utils.language import LANGUAGE_MAP, language_variants, resolve_locale, subtitle_flags
from VibraVid.core.utils.resolution import classify_resolution
from VibraVid.core.utils.selector import FilterSpec, StreamSelector, StreamSelectorFormatter
from VibraVid.core.utils.stream_selector_ui import InteractiveStreamSelector
from VibraVid.core.velora.subtitle import build_ext_track_label, ext_from_url, is_valid_format, normalize_sub_filename
from VibraVid.core.velora.util._subtitle_segments import get_subtitle_resolve_workers, resolve_subtitle_segments_sync
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)
auto_select = config_manager.config.get_bool("DOWNLOAD", "auto_select")


def lang_variants(normalized_lang: str) -> set[str]:
    """Return every LANGUAGE_MAP key that resolves to *normalized_lang*."""
    if not normalized_lang:
        return set()
    variants: set[str] = {normalized_lang, normalized_lang.lower()}
    variants.add(normalized_lang.split("-")[0].lower())
    for key, value in LANGUAGE_MAP.items():
        if value == normalized_lang or value.lower() == normalized_lang.lower():
            variants.add(key)
            variants.add(key.lower())
    return variants


class BaseMediaDownloader:
    def __init__(
        self,
        url: str,
        output_dir: str,
        filename: str,
        headers: dict | None = None,
        key: Any | None = None,
        cookies: dict | None = None,
        download_id: str | None = None,
        site_name: str | None = None,
        manifest_content: str | None = None,
        manifest_protocol: str | None = None,
        manifest_refresh_fn=None,
        has_drm: bool = False,
    ) -> None:
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.manifest_refresh_fn = manifest_refresh_fn
        self.manifest_content = manifest_content
        self.manifest_protocol = (manifest_protocol or "").lower() or None
        self.key = key
        self.has_drm = has_drm
        self.cookies = cookies or {}
        self.download_id = download_id
        self.site_name = site_name

        # Manifest and stream data
        self.streams: list[Stream] = []
        self.manifest_type: str = "Unknown"
        self.raw_m3u8: Path | None = None
        self.raw_mpd: Path | None = None
        self.raw_ism: Path | None = None
        self.raw_custom: Path | None = None
        self.status: dict | None = None

        # Custom selection filters (take precedence over config)
        self._sv: str = "best"
        self._sa: str = "best"
        self._ss: str = "all"

        # External tracks imported from manifest or added by user
        self.external_subtitles: list = []
        self.external_audios: list = []
        self.external_other_tracks: list = []
        self.other_tracks: list = []
        self.custom_filters: dict | None = None
        self.license_url: str | None = None
        self.drm_type: str | None = None

        # Manual override for HLS segment encryption (method/key_bytes/iv)
        self.hls_enc_override: dict | None = None

        # Progress-bar label tables — built in _prepare_labels()
        self._video_label: str = ""
        self._video_task_key: str = "vid_main"
        self._has_video: bool = True
        self._audio_labels: dict[str, str] = {}
        self._audio_task_keys: list[tuple[str, str]] = []
        self._sub_labels: dict[str, str] = {}
        self._sub_labels_by_task_key: dict[str, str] = {}
        self._sub_task_keys: list[tuple[str, str]] = []

        # Thread-safe output filename collision tracking (subtitle streams)
        self._assigned_sub_names: set = set()
        self._assigned_sub_lock = threading.Lock()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = self.output_dir
        self._tmp_dir.mkdir(exist_ok=True)

        if self.download_id:
            _type = (
                "Movie"
                if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
                else "TV"
                if config_manager.config.get("OUTPUT", "serie_folder_name") in str(self.output_dir)
                else "Anime"
                if config_manager.config.get("OUTPUT", "anime_folder_name") in str(self.output_dir)
                else "other"
            )
            download_tracker.start_download(self.download_id, self.filename, self.site_name or "Unknown", _type)

    def set_key(self, key: Any) -> None:
        self.key = key.get_keys_list() if isinstance(key, KeysManager) else key

    def parse_stream(self, show_table: bool = True) -> list[Stream]:
        """Fetch manifest → parse streams → apply selection → print table."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing ...")

        url_lower = self.url.lower().split("?")[0]
        proto = self.manifest_protocol
        content = self.manifest_content

        if content and proto:
            effective = proto
        elif url_lower.endswith((".mpd", ".mpp")):
            effective = "dash"
        elif url_lower.endswith(".ism") or url_lower.endswith(".ism/manifest"):
            effective = "ism"
        elif content and is_custom_manifest(content):
            effective = "custom"
        else:
            effective = proto or "hls"

        logger.info(f"Parsing manifest: {self.url!r}  auto_select={auto_select}  headers={bool(self.headers)}  protocol={effective}  injected={bool(content)}")
        if effective == "dash":
            parser = DashParser(self.url, self.headers, content=content)
        elif effective == "ism":
            parser = ISMParser(self.url, self.headers, content=content)
        elif effective == "custom":
            parser = CustomParser(self.url, self.headers, content=content)
        else:
            parser = HLSParser(self.url, self.headers, content=content, has_drm=self.has_drm)

        if not parser.fetch_manifest():
            logger.error("BaseMediaDownloader: manifest fetch failed")
            return []

        if isinstance(parser, DashParser):
            self.raw_mpd = parser.save_raw(self._tmp_dir)
            self.manifest_type = "DASH"
        elif isinstance(parser, ISMParser):
            self.raw_ism = parser.save_raw(self._tmp_dir)
            self.manifest_type = "ISM"
        elif isinstance(parser, CustomParser):
            self.raw_custom = parser.save_raw(self._tmp_dir)
            self.manifest_type = "DASH"
        else:
            self.raw_m3u8 = parser.save_raw(self._tmp_dir)
            self.manifest_type = "HLS"

        self.streams = [s for s in parser.parse_streams() if s.type != "image"]

        if auto_select:
            self._apply_selection()
        else:
            selector = InteractiveStreamSelector(self.streams, window_size=15)
            selector.run()

        for ext in self.external_subtitles:
            lang = ext.get("language", "")
            selected = self._ext_track_matches(ext, "subtitle")
            ext["_selected"] = selected
            fake = Stream(
                type="subtitle",
                language=lang,
                name=ext.get("name", ""),
                selected=selected,
                is_external=True,
            )
            fake.id = "EXT"
            self.streams.append(fake)

        for ext in self.external_audios:
            lang = ext.get("language", "")
            selected = self._ext_lang_matches(lang, "audio")
            ext["_selected"] = selected
            fake = Stream(
                type="audio",
                language=lang,
                name=ext.get("name", ""),
                selected=selected,
                is_external=True,
            )
            fake.id = "EXT"
            self.streams.append(fake)

        for ext in self.external_other_tracks:
            raw_type = (ext.get("type") or "").strip().lower()
            kind = raw_type.split(":")[0] if ":" in raw_type else raw_type
            tag = raw_type.split(":")[1] if ":" in raw_type else ""
            if kind not in ("video", "audio"):
                continue

            lang = ext.get("language", "") or ""
            if lang == "und":
                lang = ""

            fake = Stream(
                type=kind,
                language=lang,
                name=ext.get("name", ""),
                selected=True,
                is_external=True,
            )

            fake.id = "EXT"
            if kind == "video" and tag:
                tag_up = tag.upper()
                if tag_up == "DV":
                    fake.video_range = "DV"
                elif tag_up in ("HDR10", "HDR10PLUS", "HDR10+", "HDR"):
                    fake.video_range = "HDR10+"

            self.streams.append(fake)

        if show_table and self.streams:
            console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
            console.print(build_table(self.streams))

        return self.streams

    def get_metadata(self) -> tuple[str, str, str]:
        return (str(self.raw_m3u8), str(self.raw_mpd), str(self.raw_ism))

    def start_download(self) -> dict[str, Any]:
        raise NotImplementedError("Subclasses must implement start_download()")

    def _apply_selection(self) -> None:
        f = self.custom_filters or {}
        v_cfg = f.get("video") or config_manager.config.get("DOWNLOAD", "select_video")
        a_cfg = f.get("audio") or config_manager.config.get("DOWNLOAD", "select_audio")
        s_cfg = f.get("subtitle") or config_manager.config.get("DOWNLOAD", "select_subtitle")
        selector = StreamSelector(
            v_cfg,
            a_cfg,
            s_cfg,
            formatter=StreamSelectorFormatter(),
            prefer_h265=bool(f.get("prefer_h265")),
            prefer_hdr10=bool(f.get("prefer_hdr10")),
            prefer_drm=bool(f.get("prefer_drm")),
            require_drm=bool(f.get("require_drm")),
            minimum_video_height=int(f.get("minimum_video_height") or 0),
        )
        self._sv, self._sa, self._ss = selector.apply(self.streams)

    def _effective_filter(self, track_type: str) -> str:
        """Return the active selection filter for *track_type*, preferring custom_filters over config."""
        cf_key = "subtitle" if track_type == "subtitle" else "audio"
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        return (self.custom_filters or {}).get(cf_key) or config_manager.config.get("DOWNLOAD", cfg_key) or ""

    @staticmethod
    def _ext_filter_tokens(cfg: str, track_type: str) -> list[str]:
        """Split a select_audio/select_subtitle filter string into lowercase language tokens."""
        if "=" in cfg:
            spec = FilterSpec.parse(cfg, track_type)
            return [t.strip().lower() for t in (spec.langs or "").split("|") if t.strip()]
        return [t.strip().lower() for t in re.split(r"[|,]", cfg) if t.strip()]

    def _ext_lang_matches(self, lang: str, track_type: str) -> bool:
        """Return True if the external track with *lang* tag should be downloaded."""
        cfg = self._effective_filter(track_type)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False

        tokens = self._ext_filter_tokens(cfg, track_type)
        lang_l = lang.strip().lower()

        for token in tokens:
            base_token = token.split("_")[0]
            if base_token in lang_l or lang_l.startswith(base_token):
                return True

            # ISO-639-2 three-letter → two-letter prefix match ("ita" → "it")
            if len(base_token) == 3 and base_token.isalpha() and lang_l.startswith(base_token[:2]):
                return True
        return False

    def _ext_track_matches(self, track: dict, track_type: str) -> bool:
        """
        Return True if *track* (a full external track dict with flag fields)
        matches the configured selection filter, including flag requirements.
        """
        cfg = self._effective_filter(track_type)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False

        lang = (track.get("language") or "").strip().lower()
        tokens = self._ext_filter_tokens(cfg, track_type)

        for token in tokens:
            parts = token.split("_")
            base_token = parts[0]
            req_flags = {p for p in parts[1:] if p in {"forced", "cc", "sdh", "hi"}}
            if "hi" in req_flags:
                req_flags.discard("hi")
                req_flags.add("cc")

            lang_ok = (
                base_token in lang
                or lang.startswith(base_token)
                or (len(base_token) == 3 and base_token.isalpha() and lang.startswith(base_token[:2]))
            )
            if not lang_ok:
                continue

            if req_flags:
                track_flags: set = set()
                if track.get("forced"):
                    track_flags.add("forced")
                if track.get("cc"):
                    track_flags.add("cc")
                if track.get("sdh"):
                    track_flags.add("sdh")
                if not req_flags.issubset(track_flags):
                    continue

            return True

        return False

    def _prepare_labels(self) -> None:
        sel_video = [s for s in self.streams if s.type == "video" and s.selected and not s.is_external]
        sel_audio = [s for s in self.streams if s.type == "audio" and s.selected and not s.is_external]
        sel_subs = [s for s in self.streams if s.type == "subtitle" and s.selected and not s.is_external]

        # Keep only one subtitle stream per language+flag variant (e.g. "en", "en-forced", "en-sdh", etc.)
        _seen_sub_variants: set[str] = set()
        _unique_subs: list[Stream] = []
        for s in sel_subs:
            variant = f"{(s.resolved_language or s.language or 'und').lower()}{self._sub_discriminator(s)}"
            if variant in _seen_sub_variants:
                s.selected = False
                logger.info(f"Subtitle dedup: variant '{variant}' already kept — dropping duplicate format={getattr(s, 'format', '') or '?'} id={getattr(s, 'id', '')}")
                continue
            _seen_sub_variants.add(variant)
            _unique_subs.append(s)
        sel_subs = _unique_subs

        self._has_video = bool(sel_video)
        if sel_video:
            v = sel_video[0]
            codec = v.get_short_codec() or v.codecs or ""
            res = v.resolution or "main"
            parts = []

            if codec:
                parts.append(f"[yellow]\\[{codec}][/yellow]")
            if res:
                display_res = classify_resolution(v.width, v.height) if v.width and v.height else res
                parts.append(f"[white]{display_res}[/white]")
            if v.bitrate:
                parts.append(f"[blue]{v.bitrate_display}[/blue]")

            self._video_label = " ".join(parts)
            codec_key = re.sub(r"[^a-z0-9]+", "", codec.lower()) if codec else ""
            self._video_task_key = f"vid_{res}_{codec_key}" if codec_key else f"vid_{res}"
        else:
            self._video_label = ""
            self._video_task_key = "vid_main"
        self._video_labels_by_task_key = {self._video_task_key: self._video_label}

        self._audio_labels = {}
        self._audio_labels_by_task_key = {}
        self._audio_task_keys = []
        seen_normalized: set[str] = set()

        for s in sel_audio:
            lang = s.resolved_language or s.language or "und"
            codec = s.get_short_codec() or s.codecs or ""
            parts = []

            if codec:
                parts.append(f"[yellow]\\[{codec}][/yellow]")

            parts.append(f"[bold white]{lang}[/bold white]")
            if s.bitrate:
                parts.append(f"[blue]{s.bitrate_display}[/blue]")

            if s.default:
                parts.append("[bold red][DEFAULT][/bold red]")

            label = " ".join(parts)
            raw = (s.language or "und").lower()
            normalized = resolve_locale(raw) if raw else ""
            # Must match _stream_task_key's audio branch exactly (prefers resolved_language over
            # the raw, possibly-compound s.language) or the label built here won't be found by
            # key when the download actually runs, and the UI falls back to showing the raw
            # task_key instead of this label.
            key_lang = (s.resolved_language or s.language or "und").lower()
            task_lang = key_lang.split("-")[0]

            if task_lang in seen_normalized:
                logger.info(f"Audio {raw!r} already mapped as {task_lang!r} -- skip dup")
                continue
            seen_normalized.add(task_lang)

            self._audio_labels[raw] = label
            for variant in lang_variants(normalized):
                self._audio_labels[variant] = label
            if s.id and ":" in s.id:
                self._audio_labels.setdefault(s.id.split(":")[0].lower(), label)
            self._audio_labels_by_task_key[f"aud_{task_lang}"] = label

            self._audio_task_keys.append((task_lang, label))

        self._sub_labels = {}
        self._sub_labels_by_task_key = {}
        self._sub_task_keys = []
        seen_sub_keys: set[str] = set()

        for s in sel_subs:
            label = self._sub_stream_label(s)
            raw = (s.language or "und").lower()
            key_lang = (s.resolved_language or s.language or "und").lower()
            task_lang = key_lang.split("-")[0]
            task_key = f"sub_{task_lang}{self._sub_discriminator(s)}"

            name = (s.name or "").strip()
            if name:
                self._sub_labels[f"{raw}:{tmdb_client._slugify(name)}"] = label
            self._sub_labels.setdefault(raw, label)

            # Also store under base lang so "en-us" lookup finds the "en" entry
            self._sub_labels.setdefault(task_lang, label)

            # Per-task_key label lookup (used by _run_dl for correct per-stream label)
            self._sub_labels_by_task_key[task_key] = label

            if task_key not in seen_sub_keys:
                seen_sub_keys.add(task_key)
                self._sub_task_keys.append((task_key, label))

    @staticmethod
    def _sub_discriminator(stream) -> str:
        if getattr(stream, "forced", False):
            return "_forced"
        if getattr(stream, "is_sdh", False):
            return "_sdh"
        if getattr(stream, "is_cc", False):
            return "_cc"
        return ""

    @staticmethod
    def _sub_stream_label(s: "Stream") -> str:
        try:
            lang_raw = s.language or "und"
            sfx = re.search(r"[-_](forced|cc|sdh|hi)$", lang_raw, re.I)
            lang_sfx = sfx.group(1).lower() if sfx else ""
            forced = s.forced or lang_sfx == "forced"
            cc = s.is_cc or lang_sfx == "cc"
            sdh = s.is_sdh or lang_sfx == "sdh"
            default = s.default and not forced

            lang = s.resolved_language or lang_raw or "und"
            parts = [f"[bold white]{lang}[/bold white]"]
            flags = []

            if forced:
                flags.append("[FORCED]")
            if sdh:
                flags.append("[SDH]")
            if cc:
                flags.append("[CC]")
            if default:
                flags.append("[DEFAULT]")
            if flags:
                parts.append(f"[bold red]{' '.join(flags)}[/bold red]")

            if getattr(s, "is_wvtt_mp4", False):
                ext_tag = "WVTT"
            else:
                fmt = (s.format or "").lower().strip()
                _IGNORE = {"dash", ""}
                if fmt not in _IGNORE:
                    ext_tag = fmt.upper()
                else:
                    # Try to derive from codec string
                    codec_lc = (s.codecs or "").lower().strip()
                    ext_tag = SUBTITLE_CODEC_MAP.get(codec_lc, "VTT")

            return f"[yellow]\\[{ext_tag}][/yellow] {' '.join(parts)}"
        except Exception:
            return s.language or "und"

    def _get_prebuilt_tasks(self) -> list[tuple[str, str]]:
        tasks: list[tuple[str, str]] = []
        if self._has_video:
            tasks.append((self._video_task_key, f"[bold cyan]Vid[/bold cyan] {self._video_label}"))

        seen: set[str] = set()
        for lang_code, label in self._audio_task_keys:
            key = f"aud_{lang_code}"
            if key not in seen:
                seen.add(key)
                tasks.append((key, f"[bold cyan]Aud[/bold cyan] {label}"))

        # ── Subtitle media streams (wvtt-mp4 and any other non-external subs) ─
        for task_key, label in self._sub_task_keys:
            if task_key not in seen:
                seen.add(task_key)
                tasks.append((task_key, f"[bold cyan]Sub[/bold cyan] {label}"))

        return tasks

    def _resolve_external_subtitle(self, s: Stream) -> dict | None:
        sub_url = s.playlist_url or (s.segments[0].url if s.segments else None)
        if not sub_url:
            return None

        ext = ext_from_url(sub_url, "")
        segments: list[tuple[str, float]] = [(sub_url, 0.0)]
        if not ext or not is_valid_format(ext, "subtitle"):
            segments, ext = resolve_subtitle_segments_sync(sub_url, self.headers)
            if not ext or not is_valid_format(ext, "subtitle"):
                logger.info(f"Skipping external subtitle (unsupported format): {s.language} url={sub_url}")
                s.selected = False
                return None

        entry = {
            "url": segments[0][0],
            "segments": [{"url": u, "duration": d} for u, d in segments],
            "language": s.language or "und",
            "name": s.name or "",
            "forced": s.forced,
            "sdh": s.is_sdh,
            "cc": s.is_cc,
            "default": s.default,
            "type": ext,
            "_selected": True,
        }
        logger.info(f"Subtitle -> external: {s.language}  segments={len(segments)}  url={segments[0][0]}")
        s.selected = False
        return entry

    def _promote_hls_subtitles_to_external(self) -> None:
        """Move selected HLS subtitle streams into self.external_subtitles."""
        candidates = [
            s
            for s in self.streams
            if s.type == "subtitle" and s.selected and not s.is_external and self.manifest_type == "HLS"
        ]
        if not candidates:
            return

        new_ext_subs: list[dict] = []
        workers = get_subtitle_resolve_workers()
        if workers <= 1:
            for s in candidates:
                entry = self._resolve_external_subtitle(s)
                if entry:
                    new_ext_subs.append(entry)
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(candidates))) as ex:
                for entry in ex.map(self._resolve_external_subtitle, candidates):
                    if entry:
                        new_ext_subs.append(entry)

        self.external_subtitles.extend(new_ext_subs)
        if new_ext_subs:
            logger.info(f"Moved {len(new_ext_subs)} HLS subtitle(s) to external download")

    def _register_external_track_tasks(self, bar_manager: DownloadBarManager) -> None:
        """Add progress-bar tasks for every selected external subtitle/audio track."""
        seen_task_keys: dict[str, int] = {}
        for _track, _ttype in [(s, "subtitle") for s in self.external_subtitles if s.get("_selected", True)] + [
            (a, "audio") for a in self.external_audios if a.get("_selected", True)
        ]:
            _label = build_ext_track_label(_track, _ttype)
            _lang = _track.get("language", "und")
            _base_lang, _flag_suffix = normalize_sub_filename(_lang, _track)
            _base_key = f"ext_{_ttype}_{_base_lang}{_flag_suffix}"
            _count = seen_task_keys.get(_base_key, 0)
            seen_task_keys[_base_key] = _count + 1
            _task_key = _base_key if _count == 0 else f"{_base_key}_{_count + 1}"
            _track["_task_key"] = _task_key
            _track["_label"] = _label
            bar_manager.add_external_track_task(_label, _task_key)

    def _build_status(self, ext_subs: list, ext_auds: list = None) -> dict:
        status: dict[str, Any] = {
            "video": None,
            "audios": [],
            "subtitles": ext_subs or [],
            "external_subtitles": [],
            "external_audios": ext_auds or [],
            "other_tracks": list(getattr(self, "other_tracks", []) or []),
        }

        for f in sorted(self.output_dir.iterdir()):
            if not f.is_file():
                continue

            # Orphaned artifact from an interrupted normalize_timestamps() pass
            # (e.g. process killed between writing "<name>.norm.<ext>" and the
            # rename-back). Never a real track -- skip so it can't masquerade
            # as a bogus extra audio/video track.
            if ".norm." in f.name.lower():
                logger.warning(f"Skipping stray normalize artifact in output dir: {f.name}")
                continue

            ext = f.suffix.lower()
            fname_l = self.filename.lower()

            # ── video ────────────────────────────────────────────────────────
            if ext in VIDEO_EXTENSIONS and f.stem.lower() == fname_l:
                if status["video"] is None:
                    status["video"] = {"path": str(f), "size": f.stat().st_size}
                continue

            # ── audio ────────────────────────────────────────────────────────
            if ext in AUDIO_EXTENSIONS and f.name.lower().startswith(fname_l):
                if status["video"] and Path(status["video"]["path"]).name == f.name:
                    continue
                track_name = f.stem[len(self.filename) :].lstrip(".")
                status["audios"].append(
                    {
                        "path": str(f),
                        "name": track_name,
                        "size": f.stat().st_size,
                        **language_variants(track_name),
                    }
                )
                continue

            stem_lower = f.stem.lower()  # e.g. "filename.en"

            # ── wvtt-mp4 subtitles ───────────────────────────────────────────
            if ext in (".wvtt", ".mp4") and stem_lower.startswith(fname_l + "."):
                if status["video"] and Path(status["video"]["path"]).name == f.name:
                    continue
                lang_part = stem_lower[len(fname_l) + 1 :]
                status["subtitles"].append(
                    {
                        "path": str(f),
                        "language": lang_part,
                        "type": "wvtt",
                        "size": f.stat().st_size,
                        "is_wvtt_mp4": True,
                        **subtitle_flags(lang_part),
                        **language_variants(lang_part),
                    }
                )
                continue

            # ── plain-text DASH subtitles (vtt, ttml, srt) ───────────────
            # Naming: {filename}.{lang}.{ext}  e.g. "show.cs-cz.vtt"
            if ext in SUBTITLE_EXTENSIONS and stem_lower.startswith(fname_l + "."):
                lang_part = stem_lower[len(fname_l) + 1 :]  # e.g. "cs-cz"
                fmt = ext.lstrip(".")  # e.g. "vtt"
                status["subtitles"].append(
                    {
                        "path": str(f),
                        "language": lang_part,
                        "name": lang_part,
                        "type": fmt,
                        "size": f.stat().st_size,
                        **subtitle_flags(lang_part),
                        **language_variants(lang_part),
                    }
                )
                continue

        return status
