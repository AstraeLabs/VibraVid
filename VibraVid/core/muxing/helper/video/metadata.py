# 16.04.24

import json
import logging
import os
import subprocess

from VibraVid.core.muxing.helper._ffprobe_cache import ffprobe_cached
from VibraVid.core.utils.codec import get_short_codec
from VibraVid.core.utils.language import language_variants
from VibraVid.core.utils.resolution import classify_resolution as _classify_resolution
from VibraVid.setup import get_ffprobe_path

logger = logging.getLogger(__name__)


_EMPTY_METADATA = {
    "quality": "",
    "height": 0,
    "language": "",
    "video_codec": "",
    "audio_codec": "",
    "audio_tracks": [],
    "audio_flags": "",
    "sub_language": "",
    "sub_flags": "",
    "subtitle_tracks": [],
}


def _disposition_flags(disposition: dict) -> list:
    """Return the flag names (upper-case) that are set to 1 in an ffprobe stream disposition dict."""
    order = ("forced", "hearing_impaired", "visual_impaired", "comment", "default")
    labels = {"hearing_impaired": "SDH", "visual_impaired": "AD"}
    return [labels.get(name, name.upper()) for name in order if disposition.get(name)]


@ffprobe_cached
def get_media_metadata(file_path: str) -> dict:
    """Extract quality (resolution), languages, codecs and flags from a media file using ffprobe."""
    cmd = [get_ffprobe_path(), "-v", "error", "-show_streams", "-print_format", "json", file_path]
    logger.info(f"Running get_media_metadata for {os.path.basename(file_path)} with cmd: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            logger.error(f"ffprobe error while extracting metadata for file {file_path}: {result.stderr.strip()}")
            return dict(_EMPTY_METADATA)

        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        quality_val = ""
        height_val = 0
        vcodec_val = ""

        for s in streams:
            if s.get("codec_type") == "video":
                height_val = s.get("height") or 0
                quality_val = _classify_resolution(s.get("width"), height_val)

                raw_vcodec = s.get("codec_name", "")
                vcodec_val = get_short_codec("video", raw_vcodec)
                break

        languages_found = []
        acodecs_found = []
        audio_tracks = []
        audio_flags_found = []
        for s in streams:
            if s.get("codec_type") == "audio":
                lang = s.get("tags", {}).get("language") or ""
                raw_acodec = s.get("codec_name", "")
                short_acodec = get_short_codec("audio", raw_acodec)

                if lang:
                    lang_up = lang.upper()
                    if lang_up not in languages_found:
                        languages_found.append(lang_up)
                    if short_acodec and short_acodec not in acodecs_found:
                        acodecs_found.append(short_acodec)

                    flags = _disposition_flags(s.get("disposition", {}) or {})
                    audio_flags_found.extend(f for f in flags if f not in audio_flags_found)
                    audio_tracks.append(
                        {
                            "language": lang_up,
                            "codec": short_acodec,
                            "flags": flags,
                            **language_variants(lang),
                        }
                    )

        sub_languages_found = []
        sub_flags_found = []
        subtitle_tracks = []
        for s in streams:
            if s.get("codec_type") == "subtitle":
                lang = s.get("tags", {}).get("language") or ""
                if not lang:
                    continue

                lang_up = lang.upper()
                if lang_up not in sub_languages_found:
                    sub_languages_found.append(lang_up)

                disposition = s.get("disposition", {}) or {}
                forced = bool(disposition.get("forced"))
                sdh = bool(disposition.get("hearing_impaired"))
                cc = sdh  # muxer only exposes a single hearing_impaired flag; CC and SDH share it
                flags = _disposition_flags(disposition)
                sub_flags_found.extend(f for f in flags if f not in sub_flags_found)

                subtitle_tracks.append(
                    {
                        "language": lang_up,
                        "forced": forced,
                        "sdh": sdh,
                        "cc": cc,
                        "flags": flags,
                        **language_variants(lang),
                    }
                )

        return {
            "quality": quality_val,
            "height": height_val,
            "language": "-".join(languages_found) if languages_found else "",
            "video_codec": vcodec_val,
            "audio_codec": "-".join(acodecs_found) if acodecs_found else "",
            "audio_tracks": audio_tracks,
            "audio_flags": "-".join(audio_flags_found),
            "sub_language": "-".join(sub_languages_found) if sub_languages_found else "",
            "sub_flags": "-".join(sub_flags_found),
            "subtitle_tracks": subtitle_tracks,
        }

    except Exception:
        return dict(_EMPTY_METADATA)
