# 16.04.24

import json
import logging
import os
import re
import subprocess

from VibraVid.core.muxing.helper._ffprobe_cache import ffprobe_cached
from VibraVid.core.utils.codec import get_short_codec
from VibraVid.core.utils.language import language_variants
from VibraVid.core.utils.resolution import classify_resolution as _classify_resolution
from VibraVid.setup import get_ffprobe_path
from VibraVid.setup.system import get_mkvmerge_path

logger = logging.getLogger(__name__)


_EMPTY_METADATA = {
    "quality": "",
    "height": 0,
    "language": "",
    "video_codec": "",
    "video_bitrate": 0,
    "video_bitrate_source": "",
    "fps": 0.0,
    "hdr": "",
    "bit_depth": 0,
    "video_profile": "",
    "video_level": 0,
    "pix_fmt": "",
    "aspect_ratio": "",
    "field_order": "",
    "duration": 0.0,
    "size": 0,
    "audio_codec": "",
    "audio_channels": "",
    "audio_tracks": [],
    "audio_flags": "",
    "sub_language": "",
    "sub_flags": "",
    "sub_codec": "",
    "subtitle_tracks": [],
}
_PACKET_SCAN_MAX_BYTES = 8 * 1024**3


def _disposition_flags(disposition: dict) -> list:
    """Return the flag names (upper-case) that are set to 1 in an ffprobe stream disposition dict."""
    order = ("forced", "hearing_impaired", "visual_impaired", "comment", "default")
    labels = {"hearing_impaired": "SDH", "visual_impaired": "AD"}
    return [labels.get(name, name.upper()) for name in order if disposition.get(name)]


def _known_language(stream: dict) -> str:
    """Return the stream's language tag, or "" when missing or "und" (the caller lists it as UND)."""
    lang = ((stream.get("tags") or {}).get("language") or "").strip()
    return "" if lang.lower() == "und" else lang


def _parse_rate(value) -> float:
    """"24000/1001" -> 23.976 (0.0 when missing or "0/0")."""
    num, _, den = str(value or "").partition("/")
    try:
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _hms_to_seconds(value) -> float:
    """"00:25:31.989000000" -> 1531.989 (0.0 when not in that form)."""
    try:
        h, m, sec = str(value).split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except ValueError:
        return 0.0


def _stats_bitrate(stream: dict) -> int:
    """Bit rate (bps) from the stream itself, else from the statistics tags mkvmerge writes (BPS, or NUMBER_OF_BYTES over DURATION)."""
    direct = int(stream.get("bit_rate") or 0)
    if direct:
        return direct

    tags = {str(k).upper(): v for k, v in (stream.get("tags") or {}).items()}
    bps = next((v for k, v in tags.items() if k.startswith("BPS") and str(v).isdigit()), None)
    if bps:
        return int(bps)

    size = next((v for k, v in tags.items() if k.startswith("NUMBER_OF_BYTES") and str(v).isdigit()), None)
    duration = next((_hms_to_seconds(v) for k, v in tags.items() if k.startswith("DURATION")), 0.0)
    return int(int(size) * 8 / duration) if size and duration else 0


def _hdr_range(stream: dict) -> str:
    """SDR / HDR10 / HDR10+ / HLG / DV from the colour transfer and the stream side data."""
    side = " ".join(str(d.get("side_data_type", "")) for d in stream.get("side_data_list") or [])
    if "DOVI" in side:
        return "DV"
    transfer = stream.get("color_transfer") or ""
    if transfer == "smpte2084":
        return "HDR10+" if "2094-40" in side else "HDR10"
    if transfer == "arib-std-b67":
        return "HLG"
    return "SDR"


def _bit_depth(stream: dict) -> int:
    raw = str(stream.get("bits_per_raw_sample") or "")
    if raw.isdigit():
        return int(raw)
    pix_fmt = stream.get("pix_fmt") or ""
    match = re.search(r"(\d+)(?:le|be)$", pix_fmt)
    if match:
        return int(match.group(1))
    return 8 if pix_fmt else 0


def _channels_label(stream: dict) -> str:
    """"5.1(side)" -> "5.1"; stereo -> "2.0"; falls back on the channel count."""
    match = re.match(r"(\d+)\.(\d)", stream.get("channel_layout") or "")
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    count = int(stream.get("channels") or 0)
    return {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(count, f"{count}.0" if count else "")


def _mkvmerge_video(file_path: str) -> dict:
    """fps (from the track's default duration) and statistics-tag bit rate of the first video track, read with ``mkvmerge -J``."""
    try:
        result = subprocess.run([get_mkvmerge_path(), "-J", file_path], capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
        track = next(t for t in json.loads(result.stdout).get("tracks", []) if t.get("type") == "video")
    except Exception as exc:
        logger.debug(f"mkvmerge -J failed for {file_path}: {exc}")
        return {}

    props = track.get("properties") or {}
    out: dict = {}
    if int(props.get("default_duration") or 0):
        out["fps"] = round(1e9 / int(props["default_duration"]), 3)
    bps = props.get("tag_bps")
    size, duration = props.get("tag_number_of_bytes"), _hms_to_seconds(props.get("tag_duration"))
    if str(bps).isdigit():
        out["bitrate"] = int(bps)
    elif str(size).isdigit() and duration:
        out["bitrate"] = int(int(size) * 8 / duration)
    return out


def _packet_bitrate(file_path: str, duration: float) -> int:
    """Exact bit rate (bps) of the first video stream, summing its packet sizes (about 0.3 s per 250 MB; skipped on huge files)."""
    try:
        if not duration or os.path.getsize(file_path) > _PACKET_SCAN_MAX_BYTES:
            return 0
        cmd = [get_ffprobe_path(), "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=size", "-of", "csv=p=0", file_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
        total = sum(int(line) for line in result.stdout.split() if line.isdigit())
        return int(total * 8 / duration)
    except Exception as exc:
        logger.debug(f"packet bit rate scan failed for {file_path}: {exc}")
        return 0


@ffprobe_cached
def get_media_metadata(file_path: str, with_bitrate: bool = True) -> dict:
    """Extract quality (resolution), languages, codecs and flags from a media file using ffprobe. ``with_bitrate=False`` skips the packet scan that reads the whole file just to measure the video bit rate."""
    cmd = [get_ffprobe_path(), "-v", "error", "-show_streams", "-show_format", "-print_format", "json", file_path]
    logger.info(f"Running get_media_metadata for {os.path.basename(file_path)} with cmd: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            logger.error(f"ffprobe error while extracting metadata for file {file_path}: {result.stderr.strip()}")
            return dict(_EMPTY_METADATA)

        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        fmt = info.get("format") or {}
        container_bitrate = int(fmt.get("bit_rate") or 0)
        quality_val = ""
        height_val = 0
        vcodec_val = ""
        video_extra: dict = {}

        for s in streams:
            if s.get("codec_type") == "video":
                height_val = s.get("height") or 0
                quality_val = _classify_resolution(s.get("width"), height_val)

                raw_vcodec = s.get("codec_name", "")
                vcodec_val = get_short_codec("video", raw_vcodec)
                duration = float(s.get("duration") or 0) or _hms_to_seconds((s.get("tags") or {}).get("DURATION")) or float(fmt.get("duration") or 0)
                fps = _parse_rate(s.get("avg_frame_rate")) or _parse_rate(s.get("r_frame_rate"))
                vbitrate = _stats_bitrate(s)
                source = ("stream" if int(s.get("bit_rate") or 0) else "tags") if vbitrate else ""

                if (not fps or not vbitrate) and "matroska" in str(fmt.get("format_name", "")):
                    mkv = _mkvmerge_video(file_path)
                    fps = fps or mkv.get("fps", 0.0)
                    if not vbitrate and mkv.get("bitrate"):
                        vbitrate, source = mkv["bitrate"], "mkvmerge"
                
                if not vbitrate and with_bitrate:
                    vbitrate = _packet_bitrate(file_path, duration)
                    source = "packets" if vbitrate else ""
                
                if not vbitrate and container_bitrate:
                    vbitrate, source = container_bitrate, "container"

                level = int(s.get("level") or 0)
                video_extra = {
                    "video_bitrate": vbitrate,
                    "video_bitrate_source": source,
                    "fps": fps,
                    "hdr": _hdr_range(s),
                    "bit_depth": _bit_depth(s),
                    "video_profile": s.get("profile") or "",
                    "video_level": level if level > 0 else 0,
                    "pix_fmt": s.get("pix_fmt") or "",
                    "aspect_ratio": s.get("display_aspect_ratio") or "",
                    "field_order": s.get("field_order") or "",
                }
                break

        languages_found = []
        acodecs_found = []
        audio_tracks = []
        audio_flags_found = []
        for s in streams:
            if s.get("codec_type") == "audio":
                lang = _known_language(s)
                raw_acodec = s.get("codec_name", "")
                short_acodec = get_short_codec("audio", raw_acodec)

                lang_up = lang.upper()
                if lang_up and lang_up not in languages_found:
                    languages_found.append(lang_up)
                if short_acodec and short_acodec not in acodecs_found:
                    acodecs_found.append(short_acodec)

                flags = _disposition_flags(s.get("disposition", {}) or {})
                audio_flags_found.extend(f for f in flags if f not in audio_flags_found)
                audio_tracks.append(
                    {
                        "language": lang_up or "UND",
                        "codec": short_acodec,
                        "profile": s.get("profile") or "",
                        "channels": _channels_label(s),
                        "channel_layout": s.get("channel_layout") or "",
                        "sample_rate": int(s.get("sample_rate") or 0),
                        "bitrate": _stats_bitrate(s),
                        "flags": flags,
                        **language_variants(lang or "und"),
                    }
                )

        sub_languages_found = []
        sub_flags_found = []
        subtitle_tracks = []
        scodec_val = ""
        for s in streams:
            if s.get("codec_type") == "subtitle":
                if not scodec_val:
                    scodec_val = get_short_codec("subtitle", s.get("codec_name", ""))

                lang = _known_language(s)
                lang_up = lang.upper()
                if lang_up and lang_up not in sub_languages_found:
                    sub_languages_found.append(lang_up)

                disposition = s.get("disposition", {}) or {}
                forced = bool(disposition.get("forced"))
                sdh = bool(disposition.get("hearing_impaired"))
                cc = sdh  # muxer only exposes a single hearing_impaired flag; CC and SDH share it
                flags = _disposition_flags(disposition)
                sub_flags_found.extend(f for f in flags if f not in sub_flags_found)

                subtitle_tracks.append(
                    {
                        "language": lang_up or "UND",
                        "forced": forced,
                        "sdh": sdh,
                        "cc": cc,
                        "flags": flags,
                        **language_variants(lang or "und"),
                    }
                )

        return {
            **_EMPTY_METADATA,
            **video_extra,
            "quality": quality_val,
            "height": height_val,
            "language": "-".join(languages_found) if languages_found else "",
            "video_codec": vcodec_val,
            "duration": float(fmt.get("duration") or 0),
            "size": int(fmt.get("size") or 0),
            "audio_codec": "-".join(acodecs_found) if acodecs_found else "",
            "audio_channels": audio_tracks[0]["channels"] if audio_tracks else "",
            "audio_tracks": audio_tracks,
            "audio_flags": "-".join(audio_flags_found),
            "sub_language": "-".join(sub_languages_found) if sub_languages_found else "",
            "sub_flags": "-".join(sub_flags_found),
            "sub_codec": scodec_val,
            "subtitle_tracks": subtitle_tracks,
        }

    except Exception:
        return dict(_EMPTY_METADATA)
