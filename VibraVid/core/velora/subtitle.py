# 12.01.25

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from VibraVid.core.utils.codec import AUDIO_EXTENSIONS, SUBTITLE_EXTENSIONS
from VibraVid.core.utils.language import extract_lang_and_flags, language_variants, resolve_locale, subtitle_flags
from VibraVid.core.velora.bridge import run_download_plan
from VibraVid.core.velora.curl_bridge import _fetch_one
from VibraVid.core.velora.util._subtitle_segments import (
    download_and_merge_subtitle_segments,
    get_subtitle_resolve_workers,
)
from VibraVid.core.velora.util.formatting import format_size
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_async_client, get_proxy_url

logger = logging.getLogger("SubtitleDownloader")
VALID_SUBTITLE_FORMATS = {ext.lstrip(".") for ext in SUBTITLE_EXTENSIONS}
VALID_AUDIO_FORMATS = {ext.lstrip(".") for ext in AUDIO_EXTENSIONS}


def is_valid_format(fmt: str, track_type: str) -> bool:
    """Check if the detected format is valid for the given track type."""
    fmt_lower = fmt.lower()
    if track_type == "subtitle":
        return fmt_lower in VALID_SUBTITLE_FORMATS
    if track_type == "audio":
        return fmt_lower in VALID_AUDIO_FORMATS
    return False


def _extract_lang_and_flags(lang_raw: str, track_info: dict = None) -> tuple[str, set]:
    """Extract standard flags from a language string and return the clean base language and a set of flags."""
    return extract_lang_and_flags(lang_raw, track_info)


def build_ext_track_label(track: dict, track_type: str, ext_override: str = None, plain: bool = False) -> str:
    """
    Build a rich-formatted progress-bar label for an external subtitle or audio track.
    Shows language (BCP-47) + flags only — no name, no format suffix.
    """
    lang_raw = (track.get("language") or "und").strip()
    base_lang, parsed_flags = _extract_lang_and_flags(lang_raw, track)

    forced = bool(track.get("forced")) or "forced" in parsed_flags
    sdh = bool(track.get("sdh")) or "sdh" in parsed_flags
    cc = bool(track.get("cc")) or "cc" in parsed_flags

    # Suppress DEFAULT when the track is only DEFAULT because it's forced
    default = (bool(track.get("default")) or "default" in parsed_flags) and not forced
    resolved = resolve_locale(base_lang) or base_lang
    parts: list[str] = [f"[bold white]{resolved}[/bold white]"]

    flags: list[str] = []
    if forced:
        flags.append("[FORCED]")
    if sdh:
        flags.append("[SDH]")
    if cc:
        flags.append("[CC]")
    if default:
        flags.append("[DEFAULT]")

    # Use track's extension as fallback if provided
    track_ext = track.get("extension", "UNK").lower().lstrip(".")
    ext = ext_override or ext_from_url(track.get("url", ""), track_ext)
    logger.debug(f"Building label for track: lang_raw={lang_raw}, resolved={resolved}, flags={flags}, ext={ext}")

    if plain:
        plain_parts: list[str] = [resolved]
        if flags:
            plain_parts.append(" ".join(flags))

        ext_tag = f"[{ext}]" if ext else ""
        pfx = "Sub" if track_type == "subtitle" else "Aud"
        return f"{pfx} {ext_tag} {' '.join(plain_parts)}".strip()

    if flags:
        parts.append(f"[bold red]{' '.join(flags)}[/bold red]")

    ext_tag = f"[yellow]\\[{ext}][/yellow]" if ext else ""
    pfx = "[bold cyan]Sub[/bold cyan]" if track_type == "subtitle" else "[bold cyan]Aud[/bold cyan]"
    return f"{pfx} {ext_tag} {' '.join(parts)}"


_NAME_REGION_OVERRIDES = {
    "es": [
        (("latin", "419", "latino", "america", "américa"), "419"),
        (("europe", "castil", "spain", "españa", "espana"), "ES"),
    ],
    "pt": [(("brazil", "brasil"), "BR"), (("portugal",), "PT")],
    "zh": [(("simplified", "hans", "mainland"), "CN"), (("traditional", "hant", "taiwan", "hong kong"), "TW")],
    "fr": [(("canad", "quebec", "québec"), "CA"), (("france",), "FR")],
    "en": [(("british", "united kingdom", "(uk)", "(gb)"), "GB"), (("united states", "(us)"), "US")],
    "pt-br": [],
    "pt-pt": [],
    "es-419": [],
    "es-es": [],  # placeholders; primary subtag is used
}


def _apply_name_region(base_locale: str, name: str) -> str:
    """Refine the base locale using the track's name to determine a more specific region code if applicable."""
    if not name:
        return base_locale
    primary = base_locale.split("-", 1)[0]
    rules = _NAME_REGION_OVERRIDES.get(primary)
    if not rules:
        return base_locale
    name_l = name.lower()
    for needles, region in rules:
        if any(n in name_l for n in needles):
            return f"{primary}-{region}".lower()
    return base_locale


def normalize_sub_filename(lang_raw: str, track_info: dict = None) -> tuple[str, str]:
    """
    Return (base_lang, flag_suffix) for subtitle filename construction.

    Filename format: ``{filename}.{base_lang}{flag_suffix}.{ext}``
    where flag_suffix uses underscores: ``_forced``, ``_cc``, ``_sdh``, or ``""``.
    """
    base_lang, parsed_flags = _extract_lang_and_flags(lang_raw, track_info)
    base_lang = (resolve_locale(base_lang) or base_lang).lower()
    base_lang = _apply_name_region(base_lang, (track_info or {}).get("name") or "")

    flags: list[str] = []
    if (track_info and track_info.get("forced")) or "forced" in parsed_flags:
        flags.append("forced")
    if (track_info and track_info.get("sdh")) or "sdh" in parsed_flags:
        flags.append("sdh")
    if (track_info and track_info.get("cc")) or "cc" in parsed_flags or "hi" in parsed_flags:
        flags.append("cc")

    flag_str = ("_" + "_".join(flags)) if flags else ""
    return base_lang, flag_str


def ext_from_url(url: str, fallback: str = "UNK") -> str:
    """Detect subtitle/audio format from URL path, ignoring query string."""
    from VibraVid.core.velora.util._stream_helpers import _SUBTITLE_EXTENSIONS, _ext_from_url_canon

    return _ext_from_url_canon(url, _SUBTITLE_EXTENSIONS, default=fallback)


async def resolve_url(client: Any, url: str, track_type: str) -> tuple[str, str]:
    """If *url* points to an HLS manifest (#EXTM3U), resolve and return the first media segment URL and its detected format."""
    from urllib.parse import urljoin

    try:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception as exc:
        logger.error(f"resolve_url probe failed for {url!r}: {exc}")
        return url, ext_from_url(url, "UNK")

    if text.startswith("#EXTM3U"):
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                absolute_url = urljoin(url, line)
                fmt = ext_from_url(absolute_url, "UNK")
                logger.info(f"Resolved manifest -> segment: {line} (fmt={fmt})")
                return absolute_url, fmt

        logger.error(f"Manifest parsed but no segment found in {url!r}")
        return url, ext_from_url(url, "UNK")

    fmt = ext_from_url(url, "UNK")
    return url, fmt


async def _download_multi_segment_subtitle(client: Any, track: dict, out_path: Path, fmt: str) -> int | None:
    """Fetch every HLS subtitle segment for *track* and merge them into *out_path*."""
    segments = [(seg["url"], seg.get("duration", 0.0)) for seg in track["segments"]]
    merged = await download_and_merge_subtitle_segments(client, segments)
    data = merged.encode("utf-8")
    out_path.write_bytes(data)
    return len(data)


async def _process_external_track(
    client: Any,
    headers: dict,
    track: dict,
    track_type: str,
    output_dir: Path,
    bar_manager: Any,
    stop_check: Any,
    on_track_done: Callable[[str, Path | None], None] | None = None,
) -> tuple[str, dict | None]:
    lang_raw = (track.get("language") or "unknown").strip()

    # Use track's extension as fallback if provided
    track_ext = track.get("extension", "UNK").lower().lstrip(".")
    fmt: str = ext_from_url(track.get("url", ""), track_ext)
    base_lang, flag_suffix = normalize_sub_filename(lang_raw, track)
    logger.debug(
        f"Prepared to download track: lang_raw={lang_raw}, base_lang={base_lang}, flag_suffix={flag_suffix}, ext={fmt}, url={track.get('url')}"
    )

    segments: list[dict] = (track.get("segments") or []) if track_type == "subtitle" else []
    is_multi_segment = len(segments) > 1

    try:
        final_url = track.get("url", "")
        if not is_multi_segment:
            raw_url = track["url"]
            final_url, fmt = await resolve_url(client, raw_url, track_type)

            # If format is still UNK and track provides extension, use it
            if fmt == "UNK" and track.get("extension"):
                fmt = (track.get("extension") or "").lower().lstrip(".")
                logger.debug(f"Using track extension for {track_type}: {fmt}")

        if not is_valid_format(fmt, track_type):
            logger.error(f"Skipping {track_type} with invalid format '{fmt}' for {lang_raw}: {track.get('url')}")
            if on_track_done:
                on_track_done(track.get("_task_key", f"ext_{track_type}_{base_lang}{flag_suffix}"), None)
            return track_type, None

        # ── Build normalised filename ─────────────────────────────
        assigned = track.get("_assigned_sub_base") if track_type == "subtitle" else None
        if assigned:
            stem = assigned
        else:
            _bl, _fs = normalize_sub_filename(lang_raw, track)
            stem = f"{_bl}{_fs}"
        out_path = output_dir / f"{stem}.{fmt}"
        task_key = track.get("_task_key", f"ext_{track_type}_{stem}")
        new_label = build_ext_track_label(track, track_type, ext_override=fmt)
        display_label = build_ext_track_label(track, track_type, ext_override=fmt, plain=True)

        logger.info(f"Downloading external {track_type}: {lang_raw} -> {out_path.name}" + (f" ({len(segments)} segments)" if is_multi_segment else ""))

        if is_multi_segment:
            size = await _download_multi_segment_subtitle(client, track, out_path, fmt)
            size_str = format_size(size or 0)
            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "label": new_label,
                    "display_label": display_label,
                    "pct": 100,
                    "segments": f"{len(segments)}/{len(segments)}",
                    "size": f"{size_str}/{size_str}",
                    "speed": "---",
                }
            )
        else:
            plan = {
                "project": "Velora",
                "task_key": task_key,
                "label": new_label,
                "display_label": display_label,
                "concurrency": 1,
                "retry_count": config_manager.config.get_int("REQUESTS", "max_retry"),
                "timeout_seconds": config_manager.config.get_int("REQUESTS", "timeout"),
                "proxy_url": get_proxy_url(),
                "verify_tls": config_manager.config.get_bool("REQUESTS", "verify"),
                "headers": headers,
                "tasks": [
                    {
                        "task_key": task_key,
                        "label": new_label,
                        "display_label": display_label,
                        "url": final_url,
                        "path": str(out_path),
                        "headers": {},
                    }
                ],
            }
            results = run_download_plan(plan, event_cb=bar_manager.handle_progress_line, stop_check=stop_check)
            result = results[0] if results else {}
            size = int(result.get("bytes") or 0) if result.get("path") and Path(result["path"]).exists() else None

            if not size and not stop_check():
                logger.warning(f"{track_type} {lang_raw}: primary backend exhausted its retries -- falling back to curl_cffi")
                fallback_result = _fetch_one(plan["tasks"][0], plan)
                if fallback_result.get("event") == "completed" and fallback_result.get("path") and Path(fallback_result["path"]).exists():
                    size = int(fallback_result.get("bytes") or 0)
                    size_str = format_size(size)
                    bar_manager.handle_progress_line(
                        {
                            "task_key": task_key,
                            "label": new_label,
                            "display_label": display_label,
                            "pct": 100,
                            "segments": "1/1",
                            "size": f"{size_str}/{size_str}",
                            "speed": "---",
                        }
                    )

        if size:
            entry = {
                "path": str(out_path),
                "language": f"{base_lang}{flag_suffix}",
                "type": fmt,
                "size": size,
                **subtitle_flags(lang_raw, track),
                **language_variants(base_lang),
            }
            logger.info(f"Downloaded {track_type} {lang_raw}: {size} bytes -> {out_path.name}" + (f" ({len(segments)} segments)" if is_multi_segment else ""))
            if on_track_done:
                on_track_done(task_key, out_path)
            return track_type, entry

        logger.error(f"Failed to download {track_type} {lang_raw} (empty file)")
        bar_manager.handle_progress_line(
            {
                "task_key": task_key,
                "label": new_label,
                "display_label": display_label,
                "segments": "0/1",
                "speed": "FAILED",
            }
        )
        if on_track_done:
            on_track_done(task_key, None)
        return track_type, None

    except Exception as exc:
        logger.error(f"External {track_type} download failed ({track.get('language', '?')}): {exc}")
        bar_manager.handle_progress_line(
            {
                "task_key": track.get("_task_key", f"ext_{track_type}_{base_lang}{flag_suffix}"),
                "label": build_ext_track_label(track, track_type, ext_override=fmt),
                "display_label": build_ext_track_label(track, track_type, ext_override=fmt, plain=True),
                "segments": "0/1",
                "speed": "ERR",
            }
        )
        if on_track_done:
            on_track_done(track.get("_task_key", f"ext_{track_type}_{base_lang}{flag_suffix}"), None)
        return track_type, None


async def download_external_tracks_with_progress(
    headers: dict,
    external_subtitles: list[dict],
    external_audios: list[dict],
    output_dir: Path,
    filename: str,
    bar_manager: Any,
    stop_check: Any = None,
    on_track_done: Callable[[str, Path | None], None] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Download external tracks with manifest resolution, proper filenames, and progress.

    Args:
        stop_check: Optional callable that returns True when download should stop.
        on_track_done: Optional callback invoked once per track as it resolves
            (task_key, output_path_or_None) -- lets a caller (e.g. the streaming-mux
            fast path) wait on individual external tracks the same way it already
            waits on regular audio/subtitle streams.
    """
    ext_subs: list[dict] = []
    ext_auds: list[dict] = []
    all_tasks = [(sub, "subtitle") for sub in external_subtitles if sub.get("_selected", True)] + [
        (aud, "audio") for aud in external_audios if aud.get("_selected", True)
    ]

    _seen_bases: dict[str, int] = {}
    for _track, _ttype in all_tasks:
        if _ttype != "subtitle":
            continue
        _base, _flag = normalize_sub_filename((_track.get("language") or "unknown").strip(), _track)
        _stem = f"{_base}{_flag}"
        _count = _seen_bases.get(_stem, 0) + 1
        _seen_bases[_stem] = _count
        _track["_assigned_sub_base"] = _stem if _count == 1 else f"{_stem}_{_count}"

    for subs in external_subtitles:
        logger.debug(f"Add external subtitle track: {subs}")
    for auds in external_audios:
        logger.debug(f"Add external audio track: {auds}")

    if not all_tasks:
        return ext_subs, ext_auds

    workers = get_subtitle_resolve_workers()

    async with create_async_client(headers=headers) as client:
        if workers <= 1:
            results = [
                await _process_external_track(
                    client, headers, track, track_type, output_dir, bar_manager, stop_check, on_track_done
                )
                for track, track_type in all_tasks
            ]
        else:
            semaphore = asyncio.Semaphore(workers)

            async def _bounded(track: dict, track_type: str) -> tuple[str, dict | None]:
                async with semaphore:
                    return await _process_external_track(
                        client, headers, track, track_type, output_dir, bar_manager, stop_check, on_track_done
                    )

            results = await asyncio.gather(*(_bounded(track, track_type) for track, track_type in all_tasks))

    for track_type, entry in results:
        if not entry:
            continue
        if track_type == "subtitle":
            ext_subs.append(entry)
        else:
            ext_auds.append(entry)

    return ext_subs, ext_auds
