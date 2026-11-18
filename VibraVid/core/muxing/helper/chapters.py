# 15.09.26

import logging
import os
import re
import shutil
import tempfile

logger = logging.getLogger(__name__)

_GENERIC_CHAPTER_NAME_RE = re.compile(r"^chapter\s*\d+$", re.IGNORECASE)


def sort_chapters(chapters: list) -> list:
    """Return chapters ordered by their start time."""
    return sorted(chapters, key=lambda c: c["seconds"])


def dedupe_chapters(sorted_chapters: list) -> list:
    """Drop chapters sharing a start time with the previous one"""
    deduped = []
    for ch in sorted_chapters:
        if deduped and ch["seconds"] == deduped[-1]["seconds"]:
            if _GENERIC_CHAPTER_NAME_RE.match(deduped[-1].get("name", "")) and not _GENERIC_CHAPTER_NAME_RE.match(
                ch.get("name", "")
            ):
                deduped[-1] = ch
            continue
        deduped.append(ch)
    return deduped


def clamp_chapters(chapters: list, duration_seconds: float | None) -> list:
    """Drop the chapters that start at or after the end of the media (services may publish markers past the real cut)."""
    if not duration_seconds or duration_seconds <= 0:
        return chapters
    kept = [ch for ch in chapters if ch["seconds"] < duration_seconds - 1]
    return kept or chapters[:1]


def write_ffmetadata_chapters(chapters: list, duration_seconds: float | None = None) -> str:
    """Write an ffmetadata chapter file. With *duration_seconds* the last chapter ends with the media instead of being open-ended."""
    sorted_chs = dedupe_chapters(sort_chapters(clamp_chapters(chapters, duration_seconds)))
    media_end_ms = int(duration_seconds * 1000) if duration_seconds and duration_seconds > 0 else None
    lines = [";FFMETADATA1", ""]
    for i, ch in enumerate(sorted_chs):
        start_ms = ch["seconds"] * 1000
        if i + 1 < len(sorted_chs):
            end_ms = sorted_chs[i + 1]["seconds"] * 1000 - 1
        else:
            end_ms = media_end_ms if media_end_ms is not None else start_ms + 999999000
        end_ms = max(end_ms, start_ms + 1)  # guarantee END > START even if callers skip dedupe_chapters
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}", f"title={ch['name']}", ""]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ffmeta", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines))
        return f.name


def write_ogm_chapters(chapters: list, duration_seconds: float | None = None) -> str:
    sorted_chs = dedupe_chapters(sort_chapters(clamp_chapters(chapters, duration_seconds)))
    lines = []
    for i, ch in enumerate(sorted_chs, 1):
        h, rem = divmod(int(ch["seconds"]), 3600)
        m, s = divmod(rem, 60)
        lines += [f"CHAPTER{i:02d}={h:02d}:{m:02d}:{s:02d}.000", f"CHAPTER{i:02d}NAME={ch['name']}"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ogm", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines))
        return f.name


def persist_chapters_file(chapters: list, temp_dir: str | None) -> None:
    """Write a `chapters.txt` sidecar (OGM `CHAPTERxx=`/`CHAPTERxxNAME=` format)"""
    if not chapters or not temp_dir:
        return

    persist_path = os.path.join(temp_dir, "chapters.txt")
    tmp_chapter_file = write_ogm_chapters(chapters)
    try:
        shutil.copyfile(tmp_chapter_file, persist_path)
        logger.info(f"[chapters] persisted sidecar -> {persist_path}")
    except OSError as e:
        logger.warning(f"[chapters] failed to persist sidecar {persist_path}: {e}")
    finally:
        try:
            os.unlink(tmp_chapter_file)
        except OSError:
            pass
