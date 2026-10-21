# 16.04.24

import codecs
import logging
import os
import re
import subprocess

from VibraVid.setup import get_ffmpeg_path

logger = logging.getLogger(__name__)
_TIMESTAMP_RE = re.compile(r"(?:(\d{2}):)?(\d{2}):(\d{2})[.,](\d{3})")
_ASS_TIME_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})[.,](\d{2,3})$")


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


def _is_ass(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".ass", ".ssa")


def _ass_seconds(value: str) -> float | None:
    m = _ASS_TIME_RE.match(value.strip())
    if not m:
        return None
    hh, mm, ss, frac = m.groups()
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(frac) / (10 ** len(frac))


def _ass_timestamp(seconds: float) -> str:
    centis = int(round(seconds * 100))
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def _ass_events(lines: list[str]) -> list[tuple[int, float, float, int, int]]:
    """Events of an ASS/SSA script as (line_index, start, end, end_column, field_count): ``Start``/``End`` are located through the ``[Events]`` Format line."""
    events = []
    in_events = False
    start_col, end_col, fields = 1, 2, 10
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.lower() == "[events]"
        elif not in_events:
            continue
        elif stripped.lower().startswith("format:"):
            names = [n.strip().lower() for n in stripped.split(":", 1)[1].split(",")]
            if "start" in names and "end" in names:
                start_col, end_col, fields = names.index("start"), names.index("end"), len(names)
        elif stripped.lower().startswith(("dialogue:", "comment:")):
            parts = line.split(":", 1)[1].split(",", fields - 1)
            if len(parts) > max(start_col, end_col):
                start, end = _ass_seconds(parts[start_col]), _ass_seconds(parts[end_col])
                if start is not None and end is not None:
                    events.append((index, start, end, end_col, fields))
    return events


def _trim_ass_file(subtitle_path: str, max_duration: float) -> str:
    """Drop the events that start after ``max_duration`` and cut the ones that run past it (a single overrunning event still stretches the whole track)."""
    with open(subtitle_path, "rb") as f:
        data = f.read()
    had_bom = data.startswith(codecs.BOM_UTF8)
    lines = data.decode("utf-8-sig", errors="replace").splitlines(keepends=True)

    drop, cut = set(), 0
    for index, start, end, end_col, fields in _ass_events([line.rstrip("\r\n") for line in lines]):
        if start >= max_duration:
            drop.add(index)
        elif end > max_duration:
            head, _, tail = lines[index].partition(":")
            parts = tail.split(",", fields - 1)
            parts[end_col] = _ass_timestamp(max_duration)
            lines[index] = f"{head}:{','.join(parts)}"
            cut += 1

    with open(subtitle_path, "w", encoding="utf-8-sig" if had_bom else "utf-8", newline="") as f:
        f.writelines(line for i, line in enumerate(lines) if i not in drop)

    logger.info(f"ASS subtitle trimmed to {max_duration:.2f}s ({len(drop)} dropped, {cut} cut): {os.path.basename(subtitle_path)}")
    return subtitle_path


def get_subtitle_duration(subtitle_path: str) -> float | None:
    """Return the largest cue timestamp (seconds) found in an SRT/VTT file, or the latest event end of an ASS/SSA one."""
    try:
        with open(subtitle_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"get_subtitle_duration: could not read {os.path.basename(subtitle_path)}: {str(e)}")
        return None

    if _is_ass(subtitle_path):
        ends = [event[2] for event in _ass_events(content.splitlines())]
        return max(ends) if ends else None

    max_seconds = None
    for m in _TIMESTAMP_RE.finditer(content):
        hh, mm, ss, ms = m.groups()
        seconds = int(hh or 0) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0
        if max_seconds is None or seconds > max_seconds:
            max_seconds = seconds

    return max_seconds


def trim_subtitle_to_duration(subtitle_path: str, max_duration: float) -> str:
    """Drop subtitle cues past max_duration seconds, so the track doesn't overrun the video/audio it's muxed with."""
    if _is_ass(subtitle_path):
        try:
            return _trim_ass_file(subtitle_path, max_duration)
        except Exception as e:
            logger.error(f"Could not trim ASS subtitle {os.path.basename(subtitle_path)}: {str(e)}")
            return subtitle_path

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
