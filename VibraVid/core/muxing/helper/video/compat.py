# 16.04.24

import json
import logging
import os
import subprocess

from rich.console import Console

from VibraVid.core.muxing.helper._ffprobe_cache import ffprobe_cached
from VibraVid.setup import get_ffprobe_path

console = Console()
logger = logging.getLogger(__name__)
_CODEC_CONTAINER_COMPAT = {
    # Subtitle codecs
    "eia_608": {"mp4"},
    "eia_708": {"mp4"},
    "mov_text": {"mp4", "m4v"},
    "dvd_subtitle": {"mkv", "ts"},
    "hdmv_pgs_subtitle": {"mkv", "ts"},
    "subrip": {"mkv", "mp4", "ts"},
    "ass": {"mkv", "ts"},
    "webvtt": {"mkv", "mp4"},
    # Video codecs
    "h264": {"mkv", "mp4", "ts", "avi"},
    "hevc": {"mkv", "mp4", "ts"},
    "av1": {"mkv", "mp4"},
    "vp9": {"mkv", "webm"},
    "vp8": {"mkv", "webm"},
    "mpeg2video": {"mkv", "mp4", "ts", "avi"},
    "mpeg4": {"mkv", "mp4", "avi"},
    # Audio codecs
    "aac": {"mkv", "mp4", "ts", "m4a"},
    "mp3": {"mkv", "mp4", "avi", "ts"},
    "ac3": {"mkv", "mp4", "ts"},
    "eac3": {"mkv", "mp4", "ts"},
    "dts": {"mkv", "ts"},
    "flac": {"mkv"},
    "opus": {"mkv", "webm"},
    "vorbis": {"mkv", "webm"},
    "pcm_s16le": {"mkv", "avi", "wav"},
}
_PREFERRED_ORDER = ["mkv", "mp4", "ts", "avi", "webm"]


@ffprobe_cached
def get_stream_codecs(file_path: str) -> list[dict]:
    """
    Returns a list of stream info dicts (codec_name, codec_type) for the given file.

    Parameters:
        file_path (str): Path to the media file.

    Returns:
        list[dict]: e.g. [{'codec_name': 'h264', 'codec_type': 'video'}, ...]
    """
    cmd = [get_ffprobe_path(), "-v", "error", "-show_streams", "-print_format", "json", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
    logger.info(f"Running get_stream_codecs for {os.path.basename(file_path)} with cmd: {' '.join(cmd)}")

    if result.returncode != 0:
        console.print(f"[red]ffprobe error while reading codecs: {result.stderr.strip()}")
        logger.error(f"ffprobe error while reading codecs for file {file_path}: {result.stderr.strip()}")
        return []

    try:
        info = json.loads(result.stdout)
        return [
            {
                "index": s.get("index"),
                "codec_name": s.get("codec_name", "").lower(),
                "codec_type": s.get("codec_type", "").lower(),
                "language": (s.get("tags") or {}).get("language", "").lower(),
            }
            for s in info.get("streams", [])
            if s.get("codec_name")
        ]
    except json.JSONDecodeError:
        logger.error(f"JSON decode error while parsing ffprobe output for file {file_path}: {result.stdout.strip()}")
        return []


def resolve_compatible_extension(file_path: str, desired_ext: str) -> str:
    """
    Checks whether the desired output extension is compatible with all codecs in the source file. If not, returns the most compatible extension instead.

    Parameters:
        file_path (str): Path to the source media file.
        desired_ext (str): Desired output extension, with or without dot (e.g. 'mkv' or '.mkv').

    Returns:
        str: The extension to use (without dot), e.g. 'mkv' or 'mp4'.
    """
    desired_ext = desired_ext.lstrip(".").lower()
    streams = get_stream_codecs(file_path)

    if not streams:
        console.print(f"[yellow]    Warning: Could not read streams from {os.path.basename(file_path)}, keeping desired extension '{desired_ext}'")
        logger.warning(f"Could not read streams from {file_path}, keeping desired extension '{desired_ext}'")
        return desired_ext

    incompatible_codecs = []
    compatible_containers = set(_PREFERRED_ORDER)

    for stream in streams:
        codec = stream["codec_name"]
        if codec in _CODEC_CONTAINER_COMPAT:
            allowed = _CODEC_CONTAINER_COMPAT[codec]
            if desired_ext not in allowed:
                incompatible_codecs.append((stream["codec_type"], codec, allowed))
            compatible_containers &= allowed

    # If everything is compatible with the desired extension, use it
    if not incompatible_codecs:
        return desired_ext

    # Report what's incompatible
    for codec_type, codec, allowed in incompatible_codecs:
        logger.warning(f"Codec {codec} ({codec_type}) is not compatible with .{desired_ext}. Allowed containers: {', '.join(sorted(allowed))}")
        console.print(f"[yellow]    WARN [cyan]Codec [red]{codec} [cyan]({codec_type}) [cyan]is not compatible with [red].{desired_ext}[cyan]. Allowed containers: [red]{', '.join(sorted(allowed))}")

    # Pick the best compatible container in preferred order
    for preferred in _PREFERRED_ORDER:
        if preferred in compatible_containers:
            return preferred

    logger.warning(f"No fully compatible container found for file {file_path} with codecs {[s['codec_name'] for s in streams]}. Falling back to .mp4")
    console.print("[yellow]    WARN Could not find a fully compatible container, falling back to mp4")
    return "mp4"
