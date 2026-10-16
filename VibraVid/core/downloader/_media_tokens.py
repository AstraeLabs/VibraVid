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


def resolve_media_tokens(root: str, metadata: dict) -> str:
    """Fill the media-token placeholders of a name *root* (no extension) from probed *metadata*; drop the empty ones with their brackets"""
    for placeholder in MEDIA_PLACEHOLDERS:
        value = metadata.get(placeholder[2:-1], "")
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
