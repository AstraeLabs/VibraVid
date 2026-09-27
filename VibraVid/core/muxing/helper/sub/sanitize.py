# 16.04.24

import logging
import os
import re
import subprocess

from VibraVid.setup import get_ffmpeg_path

logger = logging.getLogger(__name__)
_TIMESTAMP_RE = re.compile(r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})")


def _clean_srt_tag(m: re.Match) -> str:
    """Return the tag unchanged if it is an allowed bare SRT tag, else remove it."""
    tag = m.group(2).lower()
    attrs = (m.group(3) or "").strip()
    if tag in {"i", "b", "u", "s"} and not attrs:
        return m.group(0)
    return ""


def sanitize_srt_file(subtitle_path: str) -> str:
    """Sanitize SRT subtitle files by removing invalid HTML tags. SRT only allows <i>, <b>, <u>, <s> without attributes."""
    try:
        with open(subtitle_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        logger.info(f"Sanitizing SRT: {os.path.basename(subtitle_path)}")
        sanitized_content = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)(\s[^>]*)?>").sub(_clean_srt_tag, content)

        if sanitized_content != content:
            with open(subtitle_path, "w", encoding="utf-8") as f:
                f.write(sanitized_content)
            logger.info(f"SRT sanitized: {os.path.basename(subtitle_path)}")

        return subtitle_path
    except Exception as e:
        logger.error(f"Could not sanitize SRT file {os.path.basename(subtitle_path)}: {str(e)}")
        return subtitle_path


def sanitize_vtt_file(subtitle_path: str) -> str:
    """Sanitize VTT subtitle files by replacing unmatched '<' symbols with '-'."""
    try:
        with open(subtitle_path, encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Replace unmatched '<' symbols (not followed by closing '>') with '- '
        logger.info(f"Sanitizing VTT: {os.path.basename(subtitle_path)}")
        sanitized_content = re.sub(r"<(?![^>]*>)", "- ", content)

        if sanitized_content != content:
            with open(subtitle_path, "w", encoding="utf-8") as f:
                f.write(sanitized_content)
            logger.info(f"VTT sanitized: {os.path.basename(subtitle_path)}")

        return subtitle_path
    except Exception as e:
        logger.error(f"Could not sanitize VTT file {os.path.basename(subtitle_path)}: {str(e)}")
        return subtitle_path


def get_subtitle_duration(subtitle_path: str) -> float | None:
    """Return the largest cue timestamp (seconds) found in an SRT/VTT file."""
    try:
        with open(subtitle_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"get_subtitle_duration: could not read {os.path.basename(subtitle_path)}: {str(e)}")
        return None

    max_seconds = None
    for m in _TIMESTAMP_RE.finditer(content):
        hh, mm, ss, ms = m.groups()
        seconds = int(hh or 0) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
        if max_seconds is None or seconds > max_seconds:
            max_seconds = seconds

    return max_seconds


def trim_subtitle_to_duration(subtitle_path: str, max_duration: float) -> str:
    """Drop subtitle cues past max_duration seconds, so the track doesn't overrun the video/audio it's muxed with."""
    trimmed_path = f"{subtitle_path}.trimmed{os.path.splitext(subtitle_path)[1]}"
    try:
        cmd = [get_ffmpeg_path(), "-v", "error", "-i", subtitle_path, "-t", f"{max_duration:.3f}", trimmed_path, "-y"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        logger.info(f"Running trim_subtitle_to_duration for {os.path.basename(subtitle_path)} with cmd: {' '.join(cmd)}")

        if result.returncode == 0 and os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 0:
            os.replace(trimmed_path, subtitle_path)
            logger.info(f"Subtitle trimmed to {max_duration:.2f}s: {os.path.basename(subtitle_path)}")
            return subtitle_path

        logger.warning(f"trim_subtitle_to_duration: ffmpeg failed for {os.path.basename(subtitle_path)}: {result.stderr.strip()[:200]}")
    except Exception as e:
        logger.error(f"Could not trim subtitle file {os.path.basename(subtitle_path)}: {str(e)}")
    finally:
        if os.path.exists(trimmed_path) and trimmed_path != subtitle_path:
            try:
                os.remove(trimmed_path)
            except Exception:
                pass

    return subtitle_path
