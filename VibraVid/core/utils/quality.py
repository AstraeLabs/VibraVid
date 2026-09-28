"""Video resolution labels shared by provider discovery and GUI selection."""
import re

from .resolution import classify_resolution


def normalize_quality(value: str | None) -> str:
    value = str(value or "").strip().lower()
    if value in {"", "config"}:
        return ""
    if not re.fullmatch(r"[1-9][0-9]{1,3}p", value) or int(value[:-1]) > 1080:
        raise ValueError("Invalid video quality")
    return value


def stream_quality(stream) -> str:
    width, height = getattr(stream, "width", 0), getattr(stream, "height", 0)
    if width or height:
        return classify_resolution(width, height)
    resolution = str(getattr(stream, "resolution", "") or "")
    match = re.fullmatch(r"(\d+)[xX](\d+)", resolution)
    if match:
        return classify_resolution(int(match[1]), int(match[2]))
    return resolution if re.fullmatch(r"\d+p", resolution) else ""


def manifest_qualities(url: str, headers: dict | None = None) -> list[str]:
    """Read an HLS master from the provider; never download video segments."""
    from VibraVid.core.manifest.m3u8 import HLSParser
    parser = HLSParser(url, headers)
    if not parser.fetch_manifest():
        raise ValueError("Unable to read the provider's video manifest")
    qualities = {stream_quality(s) for s in parser.parse_streams() if s.type == "video"}
    return sorted(qualities - {""}, key=lambda q: int(q[:-1]), reverse=True)
