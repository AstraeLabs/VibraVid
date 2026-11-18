# 15.09.26

import os

MEDIA_PLACEHOLDERS = (
    "%(quality)",
    "%(language)",
    "%(video_codec)",
    "%(audio_codec)",
    "%(audio_flags)",
    "%(sub_flags)",
    "%(fps)",
    "%(video_bitrate)",
    "%(hdr)",
    "%(bit_depth)",
    "%(audio_channels)",
)
FILE_PROBE_KEYS = ("fps", "video_bitrate", "hdr", "bit_depth", "audio_channels")


def media_token_values(metadata: dict) -> dict:
    """Value of every media placeholder from probed *metadata*, ready for a file name ("" when unknown, so the token and its brackets are dropped)"""
    fps = float(metadata.get("fps") or 0)
    bitrate = int(metadata.get("video_bitrate") or 0)
    depth = int(metadata.get("bit_depth") or 0)
    hdr = metadata.get("hdr") or ""
    return {
        "quality": metadata.get("quality", ""),
        "language": metadata.get("language", ""),
        "video_codec": metadata.get("video_codec", ""),
        "audio_codec": metadata.get("audio_codec", ""),
        "audio_flags": metadata.get("audio_flags", ""),
        "sub_flags": metadata.get("sub_flags", ""),
        "fps": f"{fps:g}fps" if fps else "",
        "video_bitrate": f"{round(bitrate / 1000)}kbps" if bitrate else "",
        "hdr": "" if hdr == "SDR" else hdr,
        "bit_depth": f"{depth}bit" if depth else "",
        "audio_channels": metadata.get("audio_channels", ""),
    }


def resolve_media_tokens(root: str, metadata: dict) -> str:
    """Fill the media-token placeholders of a name *root* (no extension) from probed *metadata*; drop the empty ones with their brackets"""
    values = media_token_values(metadata)
    for placeholder in MEDIA_PLACEHOLDERS:
        value = values.get(placeholder[2:-1], "")
        if value:
            root = root.replace(placeholder, str(value))
        else:
            root = root.replace(f" [{placeholder}]", "").replace(f"[{placeholder}]", "")
            root = root.replace(f" ({placeholder})", "").replace(f"({placeholder})", "")
            root = root.replace(placeholder, "")
    return root.replace("  ", " ").rstrip(" .")


def strip_media_tokens(path: str) -> str:
    """Remove unresolved media-token placeholders from *path* -- shared by BaseDownloader (segmented HLS/DASH/ISM) and MP4FileDownloader"""
    root, ext = os.path.splitext(path)
    for ph in MEDIA_PLACEHOLDERS:
        root = root.replace(f" [{ph}]", "").replace(f"[{ph}]", "")
        root = root.replace(f" ({ph})", "").replace(f"({ph})", "")
        root = root.replace(ph, "")
    root = root.replace("  ", " ").rstrip(" .")
    return root + ext
