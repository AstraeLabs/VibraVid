# 15.07.26

import logging
import os
import subprocess
from pathlib import Path

from VibraVid.setup import get_ffmpeg_path

logger = logging.getLogger(__name__)


def normalize_timestamps(src_path: Path, log: logging.Logger | None = None) -> Path | None:
    """Re-mux a media file with ffmpeg to reset absolute fragment timestamps"""
    if log is None:
        log = logger

    use_mp4 = src_path.suffix.lower() == ".m4a"
    norm_suffix = ".mp4" if use_mp4 else src_path.suffix
    norm_path = src_path.with_name(f"{src_path.stem}.norm{norm_suffix}")

    # Drop any stale ".norm." file left behind by a previous crashed/killed run
    # before writing this one -- it would otherwise sit in output_dir and get
    # misread as a bogus extra track by _build_status.
    try:
        norm_path.unlink(missing_ok=True)
    except OSError:
        pass

    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-i",
        str(src_path),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-fflags",
        "+genpts",
    ]
    if use_mp4:
        cmd += ["-f", "mp4"]
    cmd.append(str(norm_path))

    try:
        logger.info(f"Running normalize_timestamps for {os.path.basename(src_path)} with cmd: {' '.join(cmd)}")
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            encoding="utf-8",
            errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except Exception as exc:
        log.error(f"[normalize] timestamp normalization failed to run: {exc}")
        return None

    if result.returncode != 0 or not norm_path.exists() or norm_path.stat().st_size <= 0:
        log.error(f"[normalize] timestamp normalization failed (rc={result.returncode}): {(result.stderr or '')[-400:]}")
        try:
            norm_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None

    return norm_path
