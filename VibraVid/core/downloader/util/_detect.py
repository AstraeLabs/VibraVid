# 09.06.26


import logging
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _is_custom_manifest_url(url: str) -> bool:
    """True if *url* points at one of our custom JSON manifests (local file or HTTP)."""
    from VibraVid.core.manifest.custom import is_custom_manifest

    try:
        local = Path(url[7:] if url.startswith("file://") else url)
        if local.is_file():
            with open(local, encoding="utf-8", errors="replace") as fh:
                return is_custom_manifest(fh.read(4096))

        if not url.lower().startswith("http"):
            return False

        from VibraVid.utils.http_client import create_client

        with create_client(timeout=10, follow_redirects=True) as client:
            resp = client.get(url)
            return is_custom_manifest(resp.text[:4096])

    except Exception as exc:
        logger.debug(f"Custom-manifest sniff failed for {url}: {exc}")
        return False


def detect_stream_type(url: str) -> str:
    """
    Guess the stream type from the URL path.
    Falls back to a HEAD request when the extension is ambiguous.

    Returns: 'mp4' | 'hls' | 'dash' | 'ism' | 'custom' | 'unsupported'
    """
    clean = url.lower().split("?")[0].rstrip("/")

    if clean.endswith(".json") and _is_custom_manifest_url(url):
        return "custom"
    if clean.endswith((".mpd", ".mpp")):
        return "dash"
    if clean.endswith(".ism") or clean.endswith(".ism/manifest"):
        return "ism"
    if clean.endswith((".m3u8", ".m3u")):
        return "hls"
    if clean.endswith((".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m4v")):
        return "mp4"

    # Ambiguous extension: probe with a HEAD request
    try:
        from VibraVid.utils.http_client import create_client

        with create_client(timeout=10, follow_redirects=True) as client:
            resp = client.head(url)
            ct = (resp.headers.get("content-type") or "").lower()

        if "mpd" in ct or "dash" in ct:
            return "dash"
        if "mpegurl" in ct or "m3u8" in ct:
            return "hls"
        if "mp4" in ct or "video" in ct or "octet-stream" in ct:
            return "mp4"
        if "silverlight" in ct or "ism" in ct:
            return "ism"

    except Exception as exc:
        logger.debug(f"HEAD probe failed for type detection: {exc}")

    logger.warning(f"Could not detect stream type for URL, marking unsupported: {url}")
    return "unsupported"


def parse_headers(headers_list: list | None) -> dict:
    """Convert ['Key: Value', 'Key2:Value2', ...] into a plain dict."""
    result = {}
    for entry in headers_list or []:
        if ":" in entry:
            k, v = entry.split(":", 1)
            result[k.strip()] = v.strip()
        else:
            logger.warning(f"Ignoring malformed header entry (expected 'Key:Value'): {entry!r}")
    return result


def parse_raw_key(value: str | None) -> bytes | None:
    """Decode a HEX, Base64, or file-path key/IV argument into raw bytes."""
    if not value:
        return None

    value = value.strip()
    p = Path(value)

    if p.is_file():
        return p.read_bytes()

    try:
        return bytes.fromhex(value)
    except ValueError:
        import base64

        return base64.b64decode(value)


def parse_keys(key_list: list | None) -> list | None:
    """Normalise the raw ``--key`` argument(s) into a list of clean ``'kid:key'`` strings (or None if empty)."""
    if not key_list:
        return None

    from VibraVid.core.decryptor import KeysManager

    return KeysManager(key_list).get_keys_list() or None


DEFAULT_DOWNLOAD_DIR = "Video/MyDownloader"


def derive_output_path(url: str, output: str | None, extension: str) -> str:
    """Build a final output path: derive a stem from the URL when *output* is empty, and append the configured *extension* when no suffix is present."""
    output = (output or "").strip()
    if not output:
        url_path = urlparse(url).path.rstrip("/")
        stem = Path(url_path).stem or "download"
        return str(Path(DEFAULT_DOWNLOAD_DIR) / f"{stem}.{extension}")
    if not Path(output).suffix:
        return f"{output}.{extension}"
    return output
