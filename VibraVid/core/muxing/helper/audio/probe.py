# 16.04.24

import json
import logging
import os
import subprocess

from rich.console import Console

from VibraVid.core.muxing.helper._ffprobe_cache import ffprobe_cached
from VibraVid.core.muxing.helper.video.ts import is_mpegts_file
from VibraVid.setup import get_ffprobe_path

console = Console()
logger = logging.getLogger(__name__)


@ffprobe_cached
def has_audio(file_path: str) -> bool:
    """Check if a media file has an audio stream using FFprobe."""
    try:
        ffprobe_cmd = [get_ffprobe_path(), "-v", "error"]
        if is_mpegts_file(file_path):
            ffprobe_cmd += ["-f", "mpegts"]
        
        ffprobe_cmd += ["-show_streams", "-print_format", "json", file_path]
        logger.info(f"Running has_audio check for {os.path.basename(file_path)} with cmd: {' '.join(ffprobe_cmd)}")
        with subprocess.Popen(
            ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
        ) as proc:
            stdout, stderr = proc.communicate()

            if proc.returncode != 0:
                logger.error(f"Error has_audio: {stderr}")
                return False

            probe_result = json.loads(stdout)
            streams = probe_result.get("streams", [])
            for stream in streams:
                if stream.get("codec_type") == "audio":
                    return True

            logger.info(f"No audio stream found in file: {file_path}")
            return False

    except Exception as e:
        logger.error(f"Exception in has_audio: {e}")
        return False


@ffprobe_cached
def get_video_duration(file_path: str) -> float:
    """Get the duration of a media file (video or audio)."""
    if not os.path.exists(file_path):
        logger.error(f"[get_video_duration] File not found: {file_path}")
        return None

    if os.path.getsize(file_path) == 0:
        logger.error(f"[get_video_duration] File is empty: {file_path}")
        return None

    ffprobe_cmd = [get_ffprobe_path(), "-v", "error"]
    if is_mpegts_file(file_path):
        ffprobe_cmd += ["-f", "mpegts"]
    
    ffprobe_cmd += [
        "-probesize",
        "200M",
        "-analyzeduration",
        "200M",
        "-show_format",
        "-show_entries",
        "stream=codec_type,codec_name,avg_frame_rate,sample_rate,nb_frames",
        "-print_format",
        "json",
        file_path,
    ]

    logger.info(f"Running get_video_duration for {os.path.basename(file_path)} with cmd: {' '.join(ffprobe_cmd)}")
    with subprocess.Popen(
        ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    ) as proc:
        stdout, stderr = proc.communicate()

        if proc.returncode != 0:
            logger.error(f"Error get_video_duration: {stderr}")
            return None

        if not stdout:
            logger.error(f"Error get_video_duration: ffprobe produced no output (stderr: {stderr})")
            return None

        probe_result = json.loads(stdout)

        try:
            dur = float(probe_result["format"]["duration"])
        except Exception:
            logger.error(f"Error extracting duration from ffprobe output: {probe_result}")
            return 1

        size = os.path.getsize(file_path)
        corrupt = False
        if dur > 0 and size > 0:
            bitrate = (size * 8) / dur
            if bitrate < 1000:
                logger.warning(f"[get_video_duration] duration {dur:.1f}s implausible for {size} byte file (bitrate {bitrate:.0f} bit/s) — treating as corrupt")
                corrupt = True

        if dur > 0 and not corrupt:
            return dur

        # No usable container duration (missing or corrupt) -- fall back to a packet-count-based estimate.
        est = _estimate_duration_via_packet_count(file_path, is_mpegts_file(file_path))
        if est is not None and est > 0:
            logger.warning(f"[get_video_duration] {'corrupt' if corrupt else 'no usable'} container duration -- using packet-count estimate {est:.1f}s")
            return est

        if corrupt:
            return None

        return dur


@ffprobe_cached
def _estimate_duration_via_packet_count(file_path: str, is_ts: bool) -> float | None:
    """Re-probe with -count_packets to get a real frame/sample count when the container's own duration field is missing or corrupt"""
    ffprobe_cmd = [get_ffprobe_path(), "-v", "error"]
    if is_ts:
        ffprobe_cmd += ["-f", "mpegts"]
    
    ffprobe_cmd += [
        "-count_packets",
        "-show_entries",
        "stream=codec_type,codec_name,avg_frame_rate,sample_rate,nb_read_packets",
        "-print_format",
        "json",
        file_path,
    ]

    logger.info(f"Running _estimate_duration_via_packet_count for {os.path.basename(file_path)} with cmd: {' '.join(ffprobe_cmd)}")
    with subprocess.Popen(
        ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    ) as proc:
        stdout, stderr = proc.communicate()

        if proc.returncode != 0 or not stdout:
            logger.error(f"Error estimating duration via packet count: {stderr}")
            return None

        try:
            streams = json.loads(stdout).get("streams", [])
        except Exception:
            return None

        return _estimate_duration_from_frames(streams)


def _estimate_duration_from_frames(streams: list) -> float | None:
    """Estimate media duration from stream frame metadata, independent of the (possibly corrupt) container duration field."""
    for stream in streams:
        ctype = stream.get("codec_type")
        nb = stream.get("nb_read_packets") or stream.get("nb_frames")
        if not nb:
            continue
        try:
            n = int(nb)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue

        if ctype == "audio":
            sr = int(stream.get("sample_rate", 0) or 0)
            if sr <= 0:
                continue

            samples_per_frame = 1536 if stream.get("codec_name") in ("eac3", "ac3") else 1024
            return (n * samples_per_frame) / sr

        if ctype == "video":
            stream_dur = stream.get("duration")
            if stream_dur and float(stream_dur) > 0:
                return float(stream_dur)

            # Last resort: frame_count / fps, only if both are known.
            fr = stream.get("avg_frame_rate", "0/1")
            try:
                num, den = str(fr).split("/")
                fps = float(num) / float(den) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                fps = 0.0
            if fps > 0:
                return n / fps

    return None


def check_duration_v_a(video_path, audio_path, tolerance=1.0):
    """Check if the duration of the video and audio matches."""
    video_duration = get_video_duration(video_path)
    audio_duration = get_video_duration(audio_path)

    # Check if either duration is None and specify which one is None
    if video_duration is None and audio_duration is None:
        console.print("[yellow]Warning: Both video and audio durations are None. Returning 0 as duration difference.")
        logger.warning(f"Both video and audio durations are None for files: {video_path}, {audio_path}")
        return False, 0.0, 0.0, 0.0

    elif video_duration is None:
        console.print("[yellow]Warning: Video duration is None. Using audio duration for calculation.")
        logger.warning(f"Video duration is None for file: {video_path}. Using audio duration: {audio_duration} for calculation.")
        return False, 0.0, 0.0, audio_duration

    elif audio_duration is None:
        console.print("[yellow]Warning: Audio duration is None. Using video duration for calculation.")
        logger.warning(f"Audio duration is None for file: {audio_path}. Using video duration: {video_duration} for calculation.")
        return False, 0.0, video_duration, 0.0

    # Calculate the duration difference
    duration_difference = abs(video_duration - audio_duration)

    # Check if the duration difference is within the tolerance
    if duration_difference <= tolerance:
        return True, duration_difference, video_duration, audio_duration
    else:
        return False, duration_difference, video_duration, audio_duration
