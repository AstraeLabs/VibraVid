# 21.07.26

import logging
import os
import subprocess

from VibraVid.setup import get_ffmpeg_path

logger = logging.getLogger(__name__)


def cc_data_field(instream_id: str | None) -> str | None:
    """The ffmpeg ``cc_dec`` data field carrying a CEA-608 channel: CC1/CC2 travel on the first field, CC3/CC4 on the second. None (pick the first field that appears) for any other id."""
    return {"CC1": "first", "CC2": "first", "CC3": "second", "CC4": "second"}.get((instream_id or "").strip().upper())


def extract_embedded_cc(video_path: str, output_srt_path: str, instream_id: str | None = None) -> str | None:
    """Extract embedded CEA-608/708 closed captions (A53 side-data) from a video's stream into a plain .srt file. ``instream_id`` (the manifest's INSTREAM-ID) picks the channel: without it a CC3 track would get the CC1 text."""
    if not os.path.exists(video_path):
        logger.error(f"extract_embedded_cc: video not found: {video_path}")
        return None

    video_dir = os.path.dirname(os.path.abspath(video_path))
    video_name = os.path.basename(video_path)
    out_name = os.path.basename(output_srt_path)
    out_dir = os.path.dirname(os.path.abspath(output_srt_path))

    same_dir = os.path.normcase(video_dir) == os.path.normcase(out_dir)
    work_name = out_name if same_dir else f".cc_extract_{out_name}"

    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-v",
        "error",
        *(["-data_field", field] if (field := cc_data_field(instream_id)) else []),
        "-f",
        "lavfi",
        "-i",
        f"movie={video_name}[out+subcc]",
        "-map",
        "0:1",
        work_name,
    ]

    logger.info(f"Running extract_embedded_cc for {video_name} with cmd: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=video_dir, encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error(f"extract_embedded_cc: ffmpeg invocation failed: {str(e)}")
        return None

    work_path = os.path.join(video_dir, work_name)

    if result.returncode != 0:
        logger.warning(f"extract_embedded_cc: ffmpeg failed (rc={result.returncode}): {result.stderr.strip()[:300]}")
        if os.path.exists(work_path):
            try:
                os.remove(work_path)
            except Exception:
                pass
        return None

    if not os.path.exists(work_path) or os.path.getsize(work_path) == 0:
        logger.info(f"extract_embedded_cc: no caption data found in {video_name}")
        if os.path.exists(work_path):
            try:
                os.remove(work_path)
            except Exception:
                pass
        return None

    if not same_dir:
        os.makedirs(out_dir, exist_ok=True)
        os.replace(work_path, output_srt_path)
    elif work_path != output_srt_path:
        os.replace(work_path, output_srt_path)

    logger.info(f"extract_embedded_cc: extracted captions -> {os.path.basename(output_srt_path)}")
    return output_srt_path
