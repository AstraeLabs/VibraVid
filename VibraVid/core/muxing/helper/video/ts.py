# 16.04.24

import json
import logging
import os
import subprocess

from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path

logger = logging.getLogger(__name__)


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _has_mpegts_sync(buf: bytes, start: int) -> bool:
    """Check for two consecutive 0x47 sync bytes 188 bytes apart, starting at `start`."""
    end = start + 188
    return end < len(buf) and buf[start] == 0x47 and buf[end] == 0x47


def is_mpegts_file(file_path: str) -> bool:
    """
    Detect whether a file is raw MPEG-TS by its packet sync bytes.

    Returns:
        bool: True if MPEG-TS sync bytes are found (at the start, or past a leading PNG).
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(64 * 1024)
    except OSError as e:
        logger.warning(f"is_mpegts_file: could not read {file_path}: {e}")
        return False

    if _has_mpegts_sync(head, 0):
        return True

    if head.startswith(_PNG_SIGNATURE):
        for offset in range(len(head) - 188):
            if _has_mpegts_sync(head, offset):
                logger.info(f"is_mpegts_file: {file_path} starts with a PNG signature but real MPEG-TS data found at offset {offset} — treating as MPEG-TS")
                return True

    return False


def detect_ts_timestamp_issues(file_path):
    """
    Detect if a TS file has timestamp issues by checking for unset timestamps.
    Parameters:
        file_path (str): Path to the TS file.

    Returns:
        bool: True if timestamp issues are detected, False otherwise.
    """
    cmd = [
        get_ffprobe_path(),
        "-v",
        "error",
        "-show_packets",
        "-select_streams",
        "v:0",
        "-read_intervals",
        "%+#1",
        "-print_format",
        "json",
        file_path,
    ]
    logger.info(f"Running detect_ts_timestamp_issues for {os.path.basename(file_path)} with cmd: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")

    if result.returncode != 0 or "pts_time" not in result.stdout:
        logger.warning(f"ffprobe could not read timestamps for {file_path}: {result.stderr.strip()}")
        return True

    try:
        info = json.loads(result.stdout)
        packets = info.get("packets", [])
        for packet in packets:
            if packet.get("pts") is None or packet.get("pts") == "N/A":
                return True
    except json.JSONDecodeError:
        logger.error(f"JSON decode error during timestamp check: {result.stdout.strip()}")
        return True

    return False


def convert_ts_to_mp4(input_path, output_path):
    """
    Convert a TS file to MP4 to regenerate timestamps.

    Parameters:
        input_path (str): Path to the input TS file.
        output_path (str): Path to the output MP4 file.

    Returns:
        bool: True if conversion succeeded, False otherwise.
    """
    cmd = [
        get_ffmpeg_path(),
        "-fflags",
        "+genpts+igndts+discardcorrupt",
        "-avoid_negative_ts",
        "make_zero",
        "-i",
        input_path,
        "-c",
        "copy",
        "-y",
        output_path,
    ]

    logger.info(f"Running convert_ts_to_mp4 for {os.path.basename(input_path)} with cmd: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        logger.error(f"convert_ts_to_mp4 failed: {result.stderr}")

    return result.returncode == 0
