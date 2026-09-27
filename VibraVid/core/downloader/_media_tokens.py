# 15.09.26

import os

MEDIA_PLACEHOLDERS = (
    "%(quality)",
    "%(language)",
    "%(video_codec)",
    "%(audio_codec)",
    "%(audio_flags)",
    "%(sub_flags)",
)


def strip_media_tokens(path: str) -> str:
    """Remove unresolved media-token placeholders from *path* -- shared by BaseDownloader (segmented HLS/DASH/ISM) and MP4FileDownloader"""
    root, ext = os.path.splitext(path)
    for ph in MEDIA_PLACEHOLDERS:
        root = root.replace(f" [{ph}]", "").replace(f"[{ph}]", "")
        root = root.replace(f" ({ph})", "").replace(f"({ph})", "")
        root = root.replace(ph, "")
    root = root.replace("  ", " ").rstrip(" .")
    return root + ext
