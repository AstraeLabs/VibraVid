# 16.04.24

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from VibraVid.setup import get_ffmpeg_path

logger = logging.getLogger(__name__)


def _extract_mono_wav(
    input_path: str, output_wav: str, sample_rate: int, duration_seconds: float | None = None
) -> bool:
    """Extract a mono WAV from the input media file using FFmpeg, resampling to sample_rate."""
    cmd = [get_ffmpeg_path(), "-y", "-i", input_path]
    if duration_seconds:
        cmd += ["-t", str(duration_seconds)]

    cmd += [
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-vn",
        "-f",
        "wav",
        output_wav,
    ]

    logger.info(f"Running _extract_mono_wav for {os.path.basename(input_path)} with cmd: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            logger.error("_extract_mono_wav ffmpeg failed: %s", result.stderr[-400:])
            return False
        return os.path.exists(output_wav) and os.path.getsize(output_wav) > 0
    except subprocess.TimeoutExpired:
        logger.error("_extract_mono_wav: FFmpeg timeout for %s", input_path)
        return False

    except Exception as exc:
        logger.error("_extract_mono_wav exception: %s", exc)
        return False


def _to_mono(data, channel: int = 0):
    """Convert multi-channel audio data to mono by selecting a single channel."""
    if data.ndim == 1:
        return data

    ch = min(channel, data.shape[1] - 1)
    return data[:, ch]


def detect_audio_offset(
    reference_path: str,
    target_path: str,
    max_offset_seconds: float = 30.0,
    sample_rate: int = 8000,
    mono_channel: int = 0,
    analysis_window_seconds: float = 600.0,
) -> float | None:
    """Detect the time offset between two audio files by cross-correlating mono WAV extracts."""
    try:
        import numpy as np
        from scipy.io import wavfile
        from scipy.signal import correlate
    except ImportError:
        logger.warning("detect_audio_offset: scipy/numpy not available, skipping offset detection")
        return None

    tmp_dir = tempfile.mkdtemp(prefix="vv_offset_")
    try:
        ref_wav = os.path.join(tmp_dir, "ref.wav")
        tgt_wav = os.path.join(tmp_dir, "tgt.wav")

        ok_ref = _extract_mono_wav(reference_path, ref_wav, sample_rate, analysis_window_seconds)
        ok_tgt = _extract_mono_wav(target_path, tgt_wav, sample_rate, analysis_window_seconds)

        if not ok_ref or not ok_tgt:
            logger.error("detect_audio_offset: WAV extraction failed")
            return None

        sr_ref, ref_data = wavfile.read(ref_wav)
        sr_tgt, tgt_data = wavfile.read(tgt_wav)

        if sr_ref != sr_tgt:
            logger.error("detect_audio_offset: sample rate mismatch (%d vs %d)", sr_ref, sr_tgt)
            return None

        sr = sr_ref
        ref_data = _to_mono(ref_data, mono_channel)
        tgt_data = _to_mono(tgt_data, mono_channel)

        ref_norm = ref_data.astype(np.float32)
        tgt_norm = tgt_data.astype(np.float32)
        ref_norm /= np.max(np.abs(ref_norm)) or 1.0
        tgt_norm /= np.max(np.abs(tgt_norm)) or 1.0

        max_lag = int(max_offset_seconds * sr)

        logger.info("detect_audio_offset: running cross-correlation ...")
        corr = correlate(tgt_norm, ref_norm, mode="full")
        lags = np.arange(-(len(ref_norm) - 1), len(tgt_norm))

        mask = np.abs(lags) <= max_lag
        corr_masked = np.where(mask, corr, -np.inf)

        best_lag = lags[np.argmax(corr_masked)]
        offset_sec = float(best_lag) / sr

        direction = "Early" if offset_sec > 0 else "Late"
        logger.info(f"detect_audio_offset: best_lag={best_lag} samples -> offset={offset_sec:.3f} s ({Path(target_path).name} {direction} di {abs(offset_sec):.3f} s)")
        return offset_sec

    except Exception as exc:
        logger.error("detect_audio_offset exception: %s", exc, exc_info=True)
        return None

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
