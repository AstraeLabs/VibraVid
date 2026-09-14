# 10.07.26

import gzip
import logging
import re
from typing import Any
from urllib.parse import urljoin

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)

_CUE_TIME_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})\.(\d{3})(\s*-->\s*)(\d{2,}):(\d{2}):(\d{2})\.(\d{3})(.*)$")
RESTART_MAX_START = 3.0
RESTART_MIN_GAP = 15.0


def get_subtitle_resolve_workers() -> int:
    """Number of concurrent workers used to resolve/download HLS subtitle renditions. ``1`` preserves the original strictly-sequential behaviour."""
    return max(1, config_manager.config.get_int("DOWNLOAD", "subtitle_resolve_workers"))


def _ext_from_url(url: str, fallback: str = "") -> str:
    """Detect subtitle format from URL path, ignoring query string."""
    from VibraVid.core.velora.util._stream_helpers import _ext_from_url_canon

    _SUB_EXTENSIONS = ("webvtt", "vtt", "srt", "ass", "ssa", "ttml2", "ttml", "xml", "dfxp")
    return _ext_from_url_canon(url, _SUB_EXTENSIONS, default=fallback)


def parse_subtitle_playlist_segments(text: str, base_url: str) -> list[tuple[str, float]]:
    """Parse every media segment (url, duration_seconds) referenced by an HLS subtitle child playlist, in order, including segments after ``#EXT-X-DISCONTINUITY`` tags."""
    segments: list[tuple[str, float]] = []
    duration = 0.0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#EXTINF:"):
            try:
                duration = float(line[len("#EXTINF:") :].split(",", 1)[0])
            except ValueError:
                duration = 0.0
            continue

        if line.startswith("#"):
            continue

        segments.append((urljoin(base_url, line), duration))
        duration = 0.0
    return segments


def resolve_subtitle_segments_sync(url: str, headers: dict) -> tuple[list[tuple[str, float]], str]:
    """
    Synchronously probe *url* and return every subtitle segment ``(url, duration)``
    referenced by the manifest, in playback order, plus the detected extension.

    If *url* does not point to an HLS manifest, it is treated as a single segment.
    """
    try:
        hdrs = dict(headers)
        hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
        with create_client(headers=hdrs, timeout=15, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text.strip()
    except Exception as exc:
        logger.info(f"resolve_subtitle_segments_sync: request failed for {url!r}: {exc}")
        return [(url, 0.0)], _ext_from_url(url, "")

    if not text.startswith("#EXTM3U"):
        content_type = resp.headers.get("content-type", "").lower()
        for mime, ext in (
            ("vtt", "vtt"),
            ("webvtt", "vtt"),
            ("srt", "srt"),
            ("ttml", "ttml"),
            ("xml", "xml"),
            ("dfxp", "dfxp"),
        ):
            if mime in content_type:
                return [(url, 0.0)], ext
        return [(url, 0.0)], _ext_from_url(url, "")

    segments = parse_subtitle_playlist_segments(text, url)
    if not segments:
        logger.info(f"resolve_subtitle_segments_sync: manifest at {url!r} had no segments")
        return [(url, 0.0)], ""

    resolved_ext = _ext_from_url(segments[0][0], "")
    total_dur = sum(d for _, d in segments)
    logger.info(f"Resolved HLS subtitle manifest -> {len(segments)} segment(s), total {total_dur:.1f}s (ext={resolved_ext!r})")
    return segments, resolved_ext


async def download_and_merge_subtitle_segments(client: Any, segments: list[tuple[str, float]]) -> str:
    """Fetch every subtitle segment concurrently and merge them (in order) into a single WebVTT track, using each segment's nominal (EXTINF) duration to shift segments that restart their clock."""
    import asyncio

    max_retry = config_manager.config.get_int("REQUESTS", "max_retry")

    texts: list[str] = [""] * len(segments)
    durations = [dur for _url, dur in segments]

    async def _fetch(index: int, url: str) -> None:
        last_exc: Exception | None = None
        for attempt in range(max_retry + 1):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                texts[index] = resp.text
                return
            except Exception as exc:
                last_exc = exc
                if attempt < max_retry:
                    await asyncio.sleep(min(1.0 * (attempt + 1), 4.0))
        logger.warning(f"Subtitle segment failed after {max_retry + 1} attempt(s), skipping it: {url} ({last_exc})")

    await asyncio.gather(*(_fetch(i, url) for i, (url, _dur) in enumerate(segments)))
    return merge_vtt_segments(texts, durations=durations)


def merge_vtt_segments(segment_texts: list[str], durations: list[float] | None = None) -> str:
    """Concatenate consecutive in-memory WebVTT segments into a single track."""
    per_segment = [_extract_vtt_cues(text) for text in segment_texts]
    return _merge_cue_segments(per_segment, logger, durations=durations)


def _ts_to_seconds(hh: str, mm: str, ss: str, ms: str) -> float:
    """Convert a WebVTT cue timestamp to seconds."""
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _seconds_to_ts(total: float) -> str:
    """Convert seconds to a WebVTT cue timestamp string."""
    if total < 0:
        total = 0.0
    h = int(total // 3600)
    m = int((total % 3600) // 60)
    s = int(total % 60)
    ms = int(round((total - int(total)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _extract_vtt_cues(text: str):
    """Return a list of cues ``(start_sec, end_sec, timestamp_line, [text_lines])`` from one WebVTT."""
    lines = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues = []
    i, n = 0, len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("WEBVTT") or stripped.startswith("X-TIMESTAMP-MAP"):
            i += 1
            continue
        if stripped.startswith(("NOTE", "STYLE", "REGION")):
            i += 1
            while i < n and lines[i].strip():  # skip the whole block until a blank line
                i += 1
            continue
        m = _CUE_TIME_RE.match(stripped)
        if not m:  # a cue identifier line: the timestamp is next
            i += 1
            if i >= n:
                break
            m = _CUE_TIME_RE.match(lines[i].strip())
            if not m:
                continue
        start = _ts_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _ts_to_seconds(m.group(6), m.group(7), m.group(8), m.group(9))
        i += 1
        body = []
        while i < n and lines[i].strip():
            body.append(lines[i])
            i += 1
        cues.append((start, end, m, body))
    return cues


def _read_vtt_segment_text(path) -> str:
    """Read a WebVTT segment file, decompressing if it is gzipped, and return its text content."""
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def merge_vtt_files(paths, merge_logger=None) -> str:
    """Merge multiple WebVTT files into a single WebVTT track, shifting cue timestamps by"""
    log = merge_logger or logger
    per_segment = []
    for p in paths:
        try:
            text = _read_vtt_segment_text(p)
            per_segment.append(_extract_vtt_cues(text))
        except Exception as exc:
            log.warning(f"[merge_vtt] skipping unreadable segment {getattr(p, 'name', p)}: {exc}")

    return _merge_cue_segments(per_segment, log, segment_count=len(paths))


def _merge_cue_segments(
    per_segment: list[list], log, segment_count: int | None = None, durations: list[float] | None = None
) -> str:
    """Shared merge core for both ``merge_vtt_files`` and ``merge_vtt_segments``."""
    out_lines = ["WEBVTT", ""]
    running_end = 0.0
    seen = set()
    emitted = 0

    nominal_starts: list[float] | None = None
    if durations and len(durations) == len(per_segment) and any(d > 0 for d in durations):
        nominal_starts = []
        acc = 0.0
        for d in durations:
            nominal_starts.append(acc)
            acc += d

    for idx, cues in enumerate(per_segment):
        if not cues:
            continue

        first_start = cues[0][0]

        if nominal_starts is not None:
            nominal_start = nominal_starts[idx]
            dist_absolute = abs(first_start - nominal_start)
            dist_local = first_start
            seg_offset = nominal_start if dist_local < dist_absolute else 0.0
            if seg_offset > 0:
                log.debug(f"[merge_vtt] segment {idx}: duration-based restart (first_start={first_start:.2f}s, nominal_start={nominal_start:.2f}s) -> offset={seg_offset:.2f}s")
        else:
            is_real_restart = (first_start < RESTART_MAX_START) and (running_end > RESTART_MIN_GAP)
            seg_offset = running_end if is_real_restart else 0.0
            if seg_offset > 0:
                log.debug(f"[merge_vtt] segment {idx}: detected relative restart (first_start={first_start:.2f}s, running_end={running_end:.2f}s) -> offset={seg_offset:.2f}s")

        for start, end, m, body in cues:
            s, e = start + seg_offset, end + seg_offset
            text = "\n".join(body)
            key = (round(s, 3), round(e, 3), text)
            if key in seen:
                continue

            seen.add(key)
            out_lines.append(f"{_seconds_to_ts(s)}{m.group(5)}{_seconds_to_ts(e)}{m.group(10)}")
            out_lines.extend(body)
            out_lines.append("")
            running_end = max(running_end, e)
            emitted += 1

    log.info(f"[merge_vtt] merged {len(per_segment)} segment(s) -> {emitted} cue(s), final_end={running_end:.2f}s")
    if emitted == 0:
        log.warning(
            f"[merge_vtt] 0 cues extracted from {segment_count if segment_count is not None else len(per_segment)} segment(s)"
        )
    return "\n".join(out_lines) + "\n"
