# 16.04.24

import asyncio
import logging
import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rich.console import Console

from VibraVid.core.decryptor._subprocess_runner import run_with_progress
from VibraVid.core.muxing.capture import capture_ffmpeg_real_time
from VibraVid.core.muxing.helper._ffprobe_cache import ffprobe_cached
from VibraVid.core.muxing.helper.audio.probe import get_video_duration
from VibraVid.core.muxing.helper.info import Mediainfo
from VibraVid.core.muxing.helper.video import get_media_metadata, is_mpegts_file
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import resolve_ietf
from VibraVid.setup import get_dovi_tool_path, get_ffmpeg_path, get_ffprobe_path, get_mkvmerge_path
from VibraVid.utils import config_manager
from VibraVid.utils.proc import run_logged

console = Console()


logger = logging.getLogger(__name__)
CLEANUP_TMP = config_manager.config.get_bool("DOWNLOAD", "cleanup_tmp_folder")


def _run_command(cmd: list[str], description: str) -> bool:
    result = run_logged(
        cmd, description, log=logger,
        capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        if stderr:
            logger.error(f"{description} failed: {stderr}")
        elif stdout:
            logger.error(f"{description} failed: {stdout}")
        else:
            logger.error(f"{description} failed with exit code {result.returncode}")
        return False
    return True


def _rpu_profile(dovi_tool: str, rpu_file: Path) -> int | None:
    """Return the Dolby Vision profile of an extracted RPU (via ``dovi_tool info``), or None."""
    try:
        result = run_logged(
            [dovi_tool, "info", "-i", str(rpu_file), "-s"],
            "dovi_tool info (RPU profile)", log=logger,
            capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
        )
        m = re.search(r"Profile:\s*(\d+)", result.stdout or "")
        if m is None:
            logger.error(f"Could not parse RPU profile from dovi_tool info output:\n{result.stdout}")
        return int(m.group(1)) if m else None
    except Exception as exc:
        logger.warning(f"Could not determine RPU profile: {exc}")
        return None


def _dump_rpu_info(dovi_tool: str, rpu_file: Path, label: str) -> None:
    """Debug helper: dump full dovi_tool info (non -s) for a given RPU file."""
    try:
        result = run_logged(
            [dovi_tool, "info", "-i", str(rpu_file)],
            f"dovi_tool info (dump RPU for {label})", log=logger,
            capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
        )
        if result.stderr:
            logger.info(f"dovi_tool info STDERR for {label}:\n{result.stderr}")
    except Exception as exc:
        logger.warning(f"Could not dump RPU info for {label}: {exc}")


def _run_progress_command(
    cmd: list[str], label: str, input_path: Path, output_path: Path, accept_warnings: bool = False
) -> bool:
    result = run_with_progress(cmd, label, str(input_path), str(output_path))
    if isinstance(result, tuple):
        ok = bool(result[0])
        stderr = result[1] if len(result) > 1 else ""
        if not ok and accept_warnings and output_path.exists() and output_path.stat().st_size > 0:
            if stderr:
                logger.warning(f"{label} exited with warnings: {stderr[:800]}")
            return True
        if not ok and stderr:
            logger.error(f"{label} failed: {stderr[:800]}")
        return ok
    return bool(result)


def _track_language(track: dict[str, Any]) -> str:
    return resolve_ietf(track.get("language") or track.get("lang") or "und")


def _track_name(track: dict[str, Any], fallback: str) -> str:
    name = (track.get("name") or track.get("title") or fallback or "").strip()
    return name or fallback or "und"


@ffprobe_cached
def probe_media_file(file_path: str) -> dict[str, Any]:
    """Probe a media file and return rich metadata for hybrid decisions."""
    probe: dict[str, Any] = {}
    if not file_path:
        return probe

    file_obj = Path(file_path)
    if not file_obj.exists():
        return probe

    try:
        simple_probe = get_media_metadata(str(file_obj))
        if isinstance(simple_probe, dict):
            probe.update(simple_probe)
    except Exception as exc:
        logger.debug(f"Hybrid metadata probe failed for {file_obj}: {exc}")

    try:
        ffprobe_path = get_ffprobe_path()
        stream_info = asyncio.run(Mediainfo.from_file_async(ffprobe_path, str(file_obj)))
    except Exception as exc:
        logger.debug(f"Hybrid stream probe failed for {file_obj}: {exc}")
        return probe

    video_stream = next((item for item in stream_info if item.type.lower() == "video"), None)
    if video_stream:
        probe.update(
            {
                "resolution": video_stream.resolution,
                "bitrate": video_stream.bitrate,
                "fps": video_stream.fps,
                "base_info": video_stream.base_info,
                "hdr": video_stream.hdr,
                "dolby_vision": video_stream.dolby_vision,
            }
        )

    return probe


def _ffmpeg_annexb(cmd: list[str], input_path: Path, label: str) -> bool:
    duration = get_video_duration(str(input_path))
    result = capture_ffmpeg_real_time(cmd, label, duration)
    if context_tracker.should_print:
        print()
    return result.get("exit_code", -1) == 0


def _to_annexb(input_path: Path, output_path: Path) -> bool:
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-i",
        str(input_path),
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        str(output_path),
    ]
    return _ffmpeg_annexb(cmd, input_path, "[yellow]FFMPEG [cyan]Conv annexb")


def _to_annexb_hdr10(input_path: Path, output_path: Path) -> bool:
    """Convert HDR10 MP4 to Annex-B patching VUI to BT.2020/PQ.

    Streaming services often encode HEVC with bt709 in the VUI even for HDR10 content
    (actual HDR metadata lives in SEI NALs). hevc_metadata BSF patches the VUI so
    downstream tools (mkvmerge, ffprobe) read correct colour info from the bitstream."""
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-i",
        str(input_path),
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb,hevc_metadata=colour_primaries=9:transfer_characteristics=16:matrix_coefficients=9",
        str(output_path),
    ]
    print("")
    return _ffmpeg_annexb(cmd, input_path, "[yellow]FFMPEG [cyan]Conv HDR10 \\[BT.2020 PQ]")


def _dv_mp4_to_annexb(input_path: Path, output_path: Path) -> bool:
    """Convert a dvh1/dvhe MP4 to HEVC Annex-B in a single ffmpeg pass.

    ffmpeg 6.1.x maps the dvh1/dvhe FourCC to codec ID dvvideo (24), which
    hevc_mp4toannexb rejects. Forcing the input codec to hevc overrides this
    mapping so the BSF runs correctly. RPU SEI NAL units are preserved."""
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-codec:v",
        "hevc",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        str(output_path),
    ]
    return _ffmpeg_annexb(cmd, input_path, "[yellow]FFMPEG [cyan]Conv DV")


def _strip_enca(input_path: Path, output_path: Path) -> bool:
    """Re-mux an audio track through ffmpeg"""

    def _run(force_ts: bool) -> bool:
        logger.info(f"Stripping potential enca wrapper from {input_path.name} {'(forcing mpegts)' if force_ts else ''}")
        cmd = [get_ffmpeg_path(), "-y"]
        if force_ts:
            cmd += ["-f", "mpegts"]
        cmd += ["-i", str(input_path), "-map", "0:a", "-c:a", "copy", str(output_path)]
        return _run_command(cmd, f"ffmpeg strip-enca {input_path.name}{' (mpegts)' if force_ts else ''}")

    if _run(is_mpegts_file(str(input_path))):
        logger.info(f"Strip-enca successful for {input_path.name}")
        return True

    return _run(True)


def _select_hybrid_video(video_track, other_videos):
    base_path = str(video_track.get("path") or "").strip()
    if not base_path:
        return None

    other_list = list(other_videos)
    if not other_list:
        return None

    base_probe = video_track.get("probe") or probe_media_file(base_path)

    # Case A: base is HDR10, look for a DV companion track
    if base_probe.get("hdr") and not base_probe.get("dolby_vision"):
        for candidate in other_list:
            probe = candidate.get("probe") or probe_media_file(str(candidate.get("path", "")))
            candidate["probe"] = probe
            if probe.get("dolby_vision") or (candidate.get("tag") or "").lower() == "dv":
                return candidate

    # Case B: base is DV, look for an HDR10 companion track to inject RPU into
    if base_probe.get("dolby_vision"):
        for candidate in other_list:
            probe = candidate.get("probe") or probe_media_file(str(candidate.get("path", "")))
            candidate["probe"] = probe
            tag = (candidate.get("tag") or candidate.get("type") or "").lower()
            if probe.get("hdr") and not probe.get("dolby_vision"):
                return candidate
            if "hdr" in tag:
                return candidate

    # Case C: probe-independent fallback — companion explicitly tagged "dv" and base is not DV.
    if not base_probe.get("dolby_vision"):
        for candidate in other_list:
            if (candidate.get("tag") or "").lower() == "dv":
                return candidate

    return None


def build_hybrid_output(
    video_track: dict[str, Any],
    other_videos: Iterable[dict[str, Any]],
    audio_tracks: list[dict[str, Any]],
    subtitle_tracks: list[dict[str, Any]],
    output_path: str,
    filename_base: str,
) -> str | None:
    """Build a hybrid DV + HDR10 output when the media probes match the script workflow."""
    if not video_track:
        return None

    base_path_str = str(video_track.get("path") or "").strip()
    if not base_path_str:
        logger.warning("Hybrid mux skipped: base video path missing")
        return None

    base_path = Path(base_path_str)
    if not base_path.exists():
        logger.warning(f"Hybrid mux skipped: base video not found at {base_path}")
        return None

    selected_other = _select_hybrid_video(video_track, other_videos)
    if not selected_other:
        return None

    dv_path_str = str(selected_other.get("path") or "").strip()
    if not dv_path_str:
        logger.warning("Hybrid mux skipped: DV video path missing")
        return None

    dv_path = Path(dv_path_str)
    if not dv_path.exists():
        logger.warning(f"Hybrid mux skipped: DV video not found at {dv_path}")
        return None

    dovi_tool = get_dovi_tool_path()
    mkvmerge = get_mkvmerge_path()
    if not dovi_tool or not mkvmerge:
        logger.warning("Hybrid mux skipped: dovi_tool or mkvmerge not available")
        return None

    output_file = Path(output_path)
    if output_file.suffix.lower() != ".mkv":
        output_file = output_file.with_suffix(".mkv")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    work_dir = output_file.parent / f"{filename_base}_hybrid_tmp"
    work_dir.mkdir(parents=True, exist_ok=True)

    base_hevc = work_dir / f"{filename_base}_hdr.hevc"
    dv_hevc = work_dir / f"{filename_base}_dv.hevc"
    rpu_file = work_dir / f"{filename_base}_rpu.bin"
    hybrid_hevc = work_dir / f"{filename_base}_hybrid.hevc"

    # Determine which of the two tracks is actually DV and which is HDR10,
    base_probe = video_track.get("probe") or probe_media_file(base_path_str)
    other_probe = selected_other.get("probe") or probe_media_file(dv_path_str)

    if base_probe.get("dolby_vision") and not other_probe.get("dolby_vision"):
        # base track is DV, other track is HDR10 → swap roles
        actual_dv_path = base_path
        actual_hdr_path = dv_path
        logger.info("Hybrid: base=DV, other=HDR10 — extracting RPU from base, injecting into other")
    else:
        # base track is HDR10, other track is DV (normal case)
        actual_hdr_path = base_path
        actual_dv_path = dv_path
        logger.info("Hybrid: base=HDR10, other=DV — extracting RPU from other, injecting into base")

    if not _to_annexb_hdr10(actual_hdr_path, base_hevc):
        return None
    dv_is_mp4 = actual_dv_path.suffix.lower() in (".mp4", ".m4v")
    if dv_is_mp4:
        if not _dv_mp4_to_annexb(actual_dv_path, dv_hevc):
            return None
    else:
        if not _to_annexb(actual_dv_path, dv_hevc):
            return None

    if not _run_command([dovi_tool, "extract-rpu", str(dv_hevc), "-o", str(rpu_file)], "dovi_tool extract-rpu"):
        return None

    _dump_rpu_info(dovi_tool, rpu_file, "RPU grezzo (pre-conversione)")

    # The RPU must match the HDR10 base to make a valid cross-compatible file.
    src_profile = _rpu_profile(dovi_tool, rpu_file)
    convert_mode = {5: "3", 7: "2"}.get(src_profile) if src_profile is not None else None
    dv_profile_label = f"DV {src_profile}" if src_profile is not None else "DV"
    logger.debug(f"Src_profile resolved = {src_profile!r} -> convert_mode = {convert_mode!r}")
    if src_profile is None:
        logger.warning("profile rpu not detected, skipping conversion")

    if convert_mode:
        logger.info(f"Hybrid: DV source is profile {src_profile}; converting RPU to 8.1 (dovi_tool -m {convert_mode})")
        conv_result = subprocess.run(
            [dovi_tool, "-m", convert_mode, "extract-rpu", str(dv_hevc), "-o", str(rpu_file)],
            capture_output=True, text=True, check=False, encoding="utf-8", errors="replace",
        )
        logger.debug(f"Src_profile resolved = {src_profile!r} -> convert_mode = {convert_mode!r}")
        if conv_result.stdout:
            logger.info(f"Conversion stdout:\n{conv_result.stdout}")
        if conv_result.stderr:
            logger.debug(f"Conversion stderr:\n{conv_result.stderr}")
        if conv_result.returncode != 0:
            logger.error(f"dovi_tool extract-rpu (convert to 8.1) failed: {conv_result.stderr.strip()}")
            return None
        dv_profile_label = "DV 8.1"
        _dump_rpu_info(dovi_tool, rpu_file, f"RPU convertito (post -m {convert_mode})")

    dovi_label = f"[cyan]Proc[/cyan] [green]{base_hevc.name}[/green] - [yellow]DoviTool[/yellow] \\[{dv_profile_label}]"
    if not _run_progress_command(
        [
            dovi_tool,
            "inject-rpu",
            "-i",
            str(base_hevc),
            "--rpu-in",
            str(rpu_file),
            "-o",
            str(hybrid_hevc),
        ],
        dovi_label,
        base_hevc,
        hybrid_hevc,
    ):
        return None

    if output_file.exists():
        try:
            output_file.unlink()
        except OSError as exc:
            logger.warning(f"Could not remove existing hybrid output {output_file}: {exc}")

    mux_cmd: list[str] = [
        mkvmerge,
        "-o",
        str(output_file),
        "--colour-matrix",
        "0:9",
        "--colour-primaries",
        "0:9",
        "--colour-transfer-characteristics",
        "0:16",
        "--colour-range",
        "0:1",
        "--language",
        "0:und",
        "--track-name",
        "0:Hybrid DV+HDR10",
        "--compression",
        "0:none",
        str(hybrid_hevc),
    ]

    for idx, track in enumerate(audio_tracks):
        track_path_str = str(track.get("path") or "").strip()
        if not track_path_str:
            continue
        track_path = Path(track_path_str)
        if not track_path.exists():
            continue

        # Strip residual enca wrapper boxes left by live CENC segment decryption.
        lang_token = _track_language(track).replace("-", "_")
        clean_audio = work_dir / f"{filename_base}_aud{idx}_{lang_token}.mp4"
        logger.info(f"Hybrid audio[{idx}] strip-enca: {track_path.name} → {clean_audio.name}")
        if _strip_enca(track_path, clean_audio) and clean_audio.exists() and clean_audio.stat().st_size > 0:
            logger.info(f"Hybrid audio[{idx}] strip-enca OK ({clean_audio.stat().st_size // 1024} KB)")
            track_path = clean_audio
        else:
            logger.warning(f"Hybrid audio[{idx}] strip-enca failed — using original: {track_path.name}")

        mux_cmd.extend(
            [
                "--language",
                f"0:{_track_language(track)}",
                "--track-name",
                f"0:{_track_name(track, _track_language(track))}",
                str(track_path),
            ]
        )

    for track in subtitle_tracks:
        track_path_str = str(track.get("path") or "").strip()
        if not track_path_str:
            continue

        track_path = Path(track_path_str)
        if not track_path.exists():
            continue

        mux_cmd.extend(
            [
                "--language",
                f"0:{_track_language(track)}",
                "--track-name",
                f"0:{_track_name(track, _track_language(track))}",
                str(track_path),
            ]
        )

    mkv_label = f"[cyan]Mux[/cyan] [green]{output_file.name}[/green] - [yellow]MKVMerge[/yellow]"
    if not _run_progress_command(mux_cmd, mkv_label, hybrid_hevc, output_file, accept_warnings=True):
        return None

    if not output_file.exists() or output_file.stat().st_size <= 0:
        logger.error(f"Hybrid mux produced no output: {output_file}")
        return None

    logger.info(f"Hybrid output created: {output_file}")

    if CLEANUP_TMP:
        shutil.rmtree(work_dir, ignore_errors=True)

    return str(output_file)