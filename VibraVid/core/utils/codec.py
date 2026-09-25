# 13.03.26

import re

VIDEO_CODEC_MAP: dict[str, str] = {
    "avc1": "H.264",
    "h264": "H.264",
    "x264": "H.264",
    "hvc1": "H.265",
    "hev1": "H.265",
    "hevc": "H.265",
    "h265": "H.265",
    "x265": "H.265",
    "vp8": "VP8",
    "vp80": "VP8",
    "vp9": "VP9",
    "vp09": "VP9",
    "vp90": "VP9",
    "av1": "AV1",
    "av01": "AV1",
    "dvhe": "Dolby Vision",
    "dvh1": "Dolby Vision",
    "dvav": "Dolby Vision",
    "dav1": "Dolby Vision",
    "mp4v": "MPEG-4",
    "mpeg4": "MPEG-4",
    "vc1": "VC-1",
    "wmv3": "WMV",
    "mjpeg": "MJPEG",
    "prores": "ProRes",
}

AUDIO_CODEC_MAP: dict[str, str] = {
    "mp4a": "AAC",
    "aac": "AAC",
    "mp3": "MP3",
    "mp4a.69": "MP3",
    "mp4a.6b": "MP3",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "vorb": "Vorbis",
    "ac3": "AC-3",
    "ac-3": "AC-3",
    "eac3": "E-AC-3",
    "ec-3": "E-AC-3",
    "dts": "DTS",
    "dtsc": "DTS",
    "dtse": "DTS",
    "dtsh": "DTS",
    "flac": "FLAC",
    "alac": "ALAC",
    "pcm": "PCM",
    "lpcm": "PCM",
    "pcm_s16le": "PCM",
    "wma": "WMA",
    "wmav2": "WMA",
    "amr": "AMR",
    "speex": "Speex",
    "ac-4": "AC-4",
    "ac4": "AC-4",
    "ac-4.02.02.00": "AC-4",
    "mp4a.ac-4": "AC-4",
}

SUBTITLE_CODEC_MAP: dict[str, str] = {
    "stpp.ttml.im1t": "TTML",
    "stpp": "TTML",
    "ttml": "TTML",
    "wvtt": "WVTT",
    "vtt": "VTT",
    "webvtt": "VTT",
    "srt": "SRT",
    "tx3g": "SRT",
    "ass": "ASS",
    "ssa": "SSA",
    "dfxp": "TTML",
    "xml": "TTML",
}

CHANNEL_MAP: dict[str, str] = {
    "1": "Mono",
    "2": "Stereo",
    "4": "4.0",
    "6": "5.1",
    "8": "7.1",
    "A000": "Stereo",
    "A001": "Mono",
    "A002": "2.1",
    "F801": "5.1",
    "F803": "7.1",
    "F805": "7.1",
    "F809": "5.1",
}


SUBTITLE_CODEC_PREFIXES: tuple[str, ...] = (
    "wvtt",
    "stpp",
    "ttml",
    "vtt",
    "webvtt",
    "srt",
    "tx3g",
    "ass",
    "ssa",
    "dfxp",
)

AUDIO_CODEC_PREFIXES: tuple[str, ...] = (
    "mp4a",
    "ec-3",
    "ac-4",
    "ac4",
    "ac-3",
    "ac3",
    "eac3",
    "opus",
    "vorbis",
    "vorb",
    "flac",
    "alac",
    "dtsc",
    "dtse",
    "dtsh",
    "dts",
    "pcm",
    "lpcm",
    "wma",
)

VIDEO_CODEC_PREFIXES: tuple[str, ...] = (
    "avc",
    "hvc",
    "hev",
    "hevc",
    "av01",
    "vp09",
    "vp08",
    "dvhe",
    "dvh1",
    "dvav",
    "dav1",
    "mp4v",
    "vc-1",
    "vc1",
)


DV_CODEC_PREFIXES: tuple[str, ...] = ("dvh1", "dvhe", "dvav", "dav1")


def infer_video_range(codecs: str) -> str:
    """Infer the HDR/DV type from a codec string (shared by DASH and HLS parsers)."""
    c = (codecs or "").lower()
    for prefix in DV_CODEC_PREFIXES:
        if c.startswith(prefix) or f",{prefix}" in c:
            return "DV"
    if re.search(r"hvc1\.2\.|hev1\.2\.", c):
        return "HDR10"
    if re.search(r"hvc1\.8\.|hev1\.8\.", c):
        return "HDR10"
    if re.search(r"av01\.[12]\.", c):
        return "HDR10"
    return ""


VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".m4v", ".ts", ".mov", ".webm", ".m2ts", ".avi"})

AUDIO_EXTENSIONS: frozenset[str] = frozenset({".m4a", ".aac", ".mp3", ".ts", ".mp4", ".wav", ".webm", ".opus", ".flac"})

SUBTITLE_EXTENSIONS: frozenset[str] = frozenset(
    {".srt", ".vtt", ".ass", ".sub", ".ssa", ".m4s", ".ttml", ".ttml2", ".xml", ".dfxp"}
)


CODEC_EXTENSION_MAP: dict[str, str] = {
    # Video
    "avc1": "mp4",
    "hvc1": "mp4",
    "hev1": "mp4",
    "av01": "mp4",
    "vp09": "webm",
    "vp08": "webm",
    "dvhe": "mp4",
    "dvh1": "mp4",
    # Audio
    "mp4a": "m4a",
    "ec-3": "m4a",
    "ac-3": "m4a",
    "ac-4": "m4a",
    "flac": "flac",
    "alac": "m4a",
    # Song Audio
    "aac": "m4a",
    "ac3": "ac3",
    "eac3": "eac3",
    "mp3": "mp3",
    "mp3float": "mp3",
    "dts": "dts",
    "dca": "dts",
    "vorbis": "ogg",
    "pcm_s16le": "wav",
    "pcm_s24le": "wav",
    "opus": "opus",
    "libopus": "opus",
    "libvorbis": "ogg",
    "libmp3lame": "mp3",
    # Subtitle
    "wvtt": "vtt",
    "stpp": "ttml",
    "ttml": "ttml",
    "srt": "srt",
    "ass": "ass",
    "ssa": "ssa",
}


def get_codec_extension(codec_str: str, default: str = "mp4") -> str:
    """
    Return the preferred file extension for a codec string.

    Performs prefix matching (e.g. 'avc1.640028' → 'mp4').
    """
    if not codec_str:
        return default
    c = codec_str.strip().lower()
    for prefix, ext in CODEC_EXTENSION_MAP.items():
        if c.startswith(prefix):
            return ext
    return default


_VIDEO_CODEC_TOKEN: dict[str, str] = {
    "h264": "avc1",
    "h.264": "avc1",
    "avc": "avc1",
    "avc1": "avc1",
    "h265": "hvc1",
    "h.265": "hvc1",
    "hevc": "hvc1",
    "hvc1": "hvc1",
    "hev1": "hvc1",
    "av1": "av01",
    "av01": "av01",
    "vp9": "vp09",
    "vp09": "vp09",
    "vp8": "vp08",
    "vp08": "vp08",
    "dvhe": "dvhe",
    "dolby vision": "dvhe",
    "dv": "dvhe",
}

_AUDIO_CODEC_TOKEN: dict[str, str] = {
    "aac": "mp4a",
    "mp4a": "mp4a",
    "mp3": "mp4a.69",
    "ac3": "ac-3",
    "ac-3": "ac-3",
    "eac3": "ec-3",
    "e-ac-3": "ec-3",
    "ec-3": "ec-3",
    "ddplus": "ec-3",
    "dd+": "ec-3",
    "opus": "opus",
    "vorbis": "vorbis",
    "flac": "flac",
    "alac": "alac",
    "dts": "dtsc",
    "ac4": "ac-4",
    "ac-4": "ac-4",
}


def get_codec_token(user_codec: str, stream_type: str) -> str:
    """Map user label (e.g. 'H265', 'AAC') to downloader token (e.g. 'hvc1', 'mp4a')."""
    if not user_codec:
        return ""
    c = user_codec.strip().lower()
    table = _VIDEO_CODEC_TOKEN if stream_type == "video" else _AUDIO_CODEC_TOKEN
    return table.get(c, user_codec)


def _lookup(codec_map: dict, codec_str: str) -> str:
    """Exact match, then prefix match (e.g. 'avc1.640028' → 'H.264')."""
    if not codec_str:
        return ""
    c = codec_str.strip()
    c_lo = c.lower()
    for k, v in codec_map.items():
        if c_lo == k.lower():
            return v
    for k, v in codec_map.items():
        if c_lo.startswith(k.lower()):
            return v
    return c


def get_short_codec(stream_type: str, codec_str: str) -> str:
    """Return human-readable codec name given a stream type and codec string."""
    if not codec_str:
        return ""

    # Check if it's a composite codec string (contains comma)
    if "," in codec_str:
        codec_parts = [part.strip() for part in codec_str.split(",")]

        # Convert each codec based on its detected type
        converted: list[str] = []
        seen_set: set[str] = set()

        for part in codec_parts:
            detected_type = detect_stream_type(part)

            # An HLS #EXT-X-STREAM-INF's CODECS attribute lists every codec used by
            # the whole variant (video + its paired audio), not just the caller's
            # track type.
            if detected_type and detected_type != stream_type.lower():
                continue

            # Choose the appropriate codec map
            if detected_type == "video":
                codec_map = VIDEO_CODEC_MAP
            elif detected_type == "audio":
                codec_map = AUDIO_CODEC_MAP
            elif detected_type == "subtitle":
                codec_map = SUBTITLE_CODEC_MAP
            else:
                # Fallback: try the requested type
                t = stream_type.lower()
                if t == "video":
                    codec_map = VIDEO_CODEC_MAP
                elif t == "audio":
                    codec_map = AUDIO_CODEC_MAP
                elif t in ("subtitle", "text"):
                    codec_map = SUBTITLE_CODEC_MAP
                else:
                    codec_map = VIDEO_CODEC_MAP

            # Translate the codec
            translated = _lookup(codec_map, part)

            # Avoid duplicates (e.g., multiple "H.265" entries)
            if translated and translated not in seen_set:
                converted.append(translated)
                seen_set.add(translated)

        # If all conversions succeeded, return joined result
        if converted:
            return ", ".join(converted)
        return codec_str

    # Single codec - use original logic
    t = stream_type.lower()
    if t == "video":
        return _lookup(VIDEO_CODEC_MAP, codec_str)
    if t == "audio":
        return _lookup(AUDIO_CODEC_MAP, codec_str)
    if t in ("subtitle", "text"):
        return _lookup(SUBTITLE_CODEC_MAP, codec_str)
    return codec_str


def detect_stream_type(codec_str: str) -> str:
    if not codec_str:
        return ""
    c = codec_str.strip().lower()
    if any(c.startswith(p) for p in SUBTITLE_CODEC_PREFIXES):
        return "subtitle"
    if any(c.startswith(p) for p in AUDIO_CODEC_PREFIXES):
        return "audio"
    if any(c.startswith(p) for p in VIDEO_CODEC_PREFIXES):
        return "video"
    return ""


def format_bitrate(bps: int | float | None) -> str:
    """Human-readable bitrate, same formatting as Stream.bitrate_display ('1.1 Mbps', '128 Kbps')."""
    bw = bps or 0
    if bw >= 1_000_000:
        return f"{bw / 1e6:.1f} Mbps"
    if bw >= 1_000:
        return f"{bw / 1e3:.0f} Kbps"
    return f"{bw:.0f} bps" if bw else ""


def format_disposition_flags(*, forced: bool = False, sdh: bool = False, cc: bool = False, default: bool = False) -> str:
    """Rich-markup subtitle disposition tags (e.g. "[FORCED] [SDH]")."""
    default = default and not forced
    flags = []
    if forced:
        flags.append("[FORCED]")
    if sdh:
        flags.append("[SDH]")
    if cc:
        flags.append("[CC]")
    if default:
        flags.append("[DEFAULT]")
    return " ".join(flags)


def get_channel_label(channels: str) -> str:
    """Return human-readable channel layout label (e.g. '2' → 'Stereo', 'F801' → '5.1')."""
    if not channels:
        return ""
    ch = channels.strip()
    if ch in CHANNEL_MAP:
        return CHANNEL_MAP[ch]
    try:
        n = int(float(ch))
        return CHANNEL_MAP.get(str(n), ch)
    except (ValueError, TypeError):
        return ch
