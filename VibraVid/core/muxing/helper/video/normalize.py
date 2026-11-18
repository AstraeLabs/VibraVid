# 15.07.26

import logging
import os
import subprocess
from pathlib import Path

from VibraVid.setup import get_ffmpeg_path

logger = logging.getLogger(__name__)


def _normalize_with_flux(src_path: Path, flux_path: str, log: logging.Logger) -> Path | None:
    """Rewrite a fragmented MP4 as a progressive MP4 with flux (same container fix as the ffmpeg re-mux, no ffmpeg run). None when flux cannot do it."""
    norm_path = src_path.with_name(f"{src_path.stem}.norm.mp4")
    try:
        norm_path.unlink(missing_ok=True)
    except OSError:
        pass

    cmd = [flux_path, str(src_path), "-o", str(norm_path), "-f", "progressive"]
    try:
        log.info(f"Running flux progressive for {os.path.basename(src_path)}")
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
        log.warning(f"[normalize] flux progressive failed to run, using ffmpeg: {exc}")
        return None

    if result.returncode != 0 or not norm_path.exists() or norm_path.stat().st_size <= 0:
        log.warning(f"[normalize] flux progressive failed (rc={result.returncode}), using ffmpeg: {(result.stderr or '')[-300:]}")
        try:
            norm_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return norm_path


def normalize_timestamps(src_path: Path, log: logging.Logger | None = None, flux_path: str | None = None) -> Path | None:
    """Re-mux a media file to reset absolute fragment timestamps. With *flux_path* flux does it (the caller guarantees a clear init and contiguous fragments: flux closes timestamp gaps), ffmpeg otherwise or if flux fails."""
    if log is None:
        log = logger

    if flux_path:
        flux_result = _normalize_with_flux(src_path, flux_path, log)
        if flux_result is not None:
            return flux_result

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
