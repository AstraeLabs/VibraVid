# 05.05.26

import json
import logging
import subprocess
from pathlib import Path

from VibraVid.setup import get_ffprobe_path, get_flux_path
from VibraVid.utils.os import os_manager

logger = logging.getLogger(__name__)
_FLUX_DUMP_SCAN_BYTES = 1 * 1024 * 1024  # 1 MB


def _ffprobe_streams(ffprobe: str, file_path: str) -> tuple[bool, str]:
    """Return (ok, message). ok=True means at least one decodable stream."""
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_streams",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "ffprobe timed out"

    except Exception as exc:
        return False, f"ffprobe failed to launch: {exc}"

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return False, f"ffprobe exit={result.returncode}: {output.strip()[:200]}"

    streams: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if line == "[STREAM]":
            current = {}
        elif line == "[/STREAM]":
            streams.append(current)
            current = {}
        elif "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()

    if not streams:
        return False, "ffprobe reported no streams"

    media_streams = [s for s in streams if s.get("codec_type", "") in {"video", "audio", "subtitle"}]
    if not media_streams:
        codec_names = ", ".join(s.get("codec_name", "?") for s in streams) or "(none)"
        return False, f"no audio/video stream (codec_type=data only): {codec_names}"

    # Only video/audio streams indicate encryption when reported as unknown.
    av_streams = [s for s in media_streams if s.get("codec_type", "") in {"video", "audio"}]
    bad = [s for s in av_streams if s.get("codec_name", "unknown") in {"unknown", "none", ""}]
    if bad:
        return False, "ffprobe still reports unknown codec — file likely encrypted"

    summary = ", ".join(f"{s.get('codec_type', '?')}={s.get('codec_name', '?')}" for s in media_streams)
    return True, summary


def _flux_dump_clean(file_path: str) -> tuple[bool, str]:
    """Return (clean, message). clean=True means no residual encryption boxes."""
    try:
        with open(file_path, "rb") as fh:
            head = fh.read(_FLUX_DUMP_SCAN_BYTES)

        with os_manager.temp_binary_file(head, suffix=".mp4") as tmp_path:
            result = subprocess.run(
                [get_flux_path(), "--dump", "-j", "-i", tmp_path],
                capture_output=True,
                text=True,
                timeout=30,
            )

    except Exception as exc:
        return True, f"flux --dump failed: {exc} (skipped)"

    if result.returncode != 0 or not result.stdout:
        return True, "flux --dump produced no output (skipped)"

    try:
        streams = json.loads(result.stdout).get("streams", [])
    except Exception as exc:
        return True, f"flux --dump JSON parse failed: {exc} (skipped)"

    # residual_protection_boxes covers the same definitive "this track is still declared CENC-protected"
    if any(s.get("residual_protection_boxes") for s in streams):
        return False, "residual encryption boxes present"
    return True, "no residual encryption boxes"


def verify_decrypted_media(file_path) -> tuple[bool, str, bool]:
    """Verify that *file_path* is a playable, fully decrypted media file."""
    p = Path(file_path)
    if not p.exists():
        return False, "output file missing", False

    if p.stat().st_size == 0:
        return False, "output file is empty", False

    ok, ffprobe_msg = _ffprobe_streams(get_ffprobe_path(), str(p))
    if not ok:
        return False, ffprobe_msg, "encrypted" in ffprobe_msg.lower()

    clean, dump_msg = _flux_dump_clean(str(p))
    if not clean:
        return False, f"{ffprobe_msg}; {dump_msg}", True
    return True, f"{ffprobe_msg}; {dump_msg}", False
