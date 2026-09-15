# 16.04.24

import logging
import os
import subprocess
from pathlib import Path

from VibraVid.core.decryptor._models import detect_encryption_info
from VibraVid.core.utils.codec import get_codec_extension
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path

logger = logging.getLogger(__name__)


def audio_ext_for_codec(codec: str) -> str | None:
    """Map an audio codec name to a file extension, or None if unknown."""
    if not codec:
        return None
    return get_codec_extension(codec.strip().lower(), default="")


def _detect_output_ext(ffmpeg_params: list[str], fallback_ext: str) -> str:
    """
    Derive the output file extension from the -c:a codec in ffmpeg_params.
    Falls back to fallback_ext if -c:a is absent or unrecognised.
    """
    try:
        idx = ffmpeg_params.index("-c:a") + 1
        codec = ffmpeg_params[idx]
        return get_codec_extension(codec, default=fallback_ext)
    except (ValueError, IndexError):
        return fallback_ext


def _detect_audio_codec(path: str) -> str:
    """First audio stream's codec_name via ffprobe, lowercased ('' if undetectable)."""
    ffprobe_path = get_ffprobe_path()
    if not ffprobe_path:
        return ""
    
    try:
        logger.info(f"Running ffprobe to detect audio codec for {os.path.basename(path)} with cmd: {ffprobe_path} -v error -select_streams a:0 -show_entries stream=codec_name -of default=noprint_wrappers=1:nokey=1 {path}")
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return (result.stdout or "").strip().lower().split(",")[0]
    except Exception:
        logger.exception(f"[audio] codec probe failed for {os.path.basename(path)}")
        return ""


def fix_container_mismatch(path: str) -> str:
    """Rename/remux *path* so its extension matches the audio codec actually inside it."""
    codec = _detect_audio_codec(path)
    if not codec:
        return path

    correct_ext = get_codec_extension(codec, default="")
    current_ext = Path(path).suffix.lstrip(".").lower()
    if not correct_ext or correct_ext == current_ext:
        return path

    if detect_encryption_info(path).encrypted:
        logger.warning(f"[audio] container mismatch fix skipped — {os.path.basename(path)} is still encrypted")
        return path

    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        logger.warning(f"[audio] ffmpeg missing — cannot fix container mismatch (.{current_ext} holding {codec})")
        return path

    fixed_path = str(Path(path).with_suffix(f".{correct_ext}"))
    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        path,
        "-map",
        "0:a:0",
        "-map_metadata",
        "-1",
        "-c:a",
        "copy",
        "-f",
        correct_ext,
        fixed_path,
    ]
    
    logger.info(f"Running container mismatch fix for {os.path.basename(path)} with cmd: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
        if result.returncode == 0 and os.path.exists(fixed_path) and os.path.getsize(fixed_path) > 0:
            os.remove(path)
            logger.info(f"[audio] container mismatch fixed: .{current_ext} -> .{correct_ext} ({codec})")
            return fixed_path
        logger.error(f"[audio] container fix failed (rc={result.returncode}): {(result.stderr or '')[-300:]}")
    except Exception:
        logger.exception(f"[audio] container fix crashed for {os.path.basename(path)}")

    return path
