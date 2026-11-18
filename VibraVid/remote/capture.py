# 12.10.26

import logging
import threading
from contextlib import contextmanager

from VibraVid.core.ui.tracker import context_tracker

from .models import ResolvedMedia

logger = logging.getLogger(__name__)

SERVER_SIDE_CALLABLES = ("license_request_fn", "manifest_refresh_fn")


class CaptureBuffer:
    """Collects what the downloaders would have downloaded while ``context_tracker.capture`` is set."""
    def __init__(self):
        self.items: list[ResolvedMedia] = []
        self.callables: list[dict] = []
        self.errors: list[str] = []
        self._lock = threading.Lock()

    def add(self, item: ResolvedMedia, hooks: dict | None = None) -> None:
        with self._lock:
            self.items.append(item)
            self.callables.append(hooks or {})

    def fail(self, message: str) -> None:
        with self._lock:
            self.errors.append(message)


def reject_if_capturing(label: str) -> str | None:
    """For downloaders with no remote form: while capturing, record why and return the message (else ``None``)."""
    buffer = context_tracker.capture
    if buffer is None:
        if context_tracker.export_file is not None:
            return f"{label} cannot be exported: --export covers MP4, HLS, DASH and ISM downloads"
        return None
    message = f"{context_tracker.site_name or 'this service'} uses {label}, which is not supported through a remote server"
    buffer.fail(message)
    return message


def _kind_and_manifest(downloader) -> tuple[str, str | None, str | None, dict | None]:
    if hasattr(downloader, "m3u8_url"):
        return "hls", downloader.m3u8_url, downloader.m3u8_content, downloader.headers
    if hasattr(downloader, "mpd_url"):
        return "dash", downloader.mpd_url, downloader.mpd_content, downloader.mpd_headers
    if hasattr(downloader, "ism_url"):
        return "ism", downloader.ism_url, downloader.ism_content, downloader.headers
    raise ValueError(f"{type(downloader).__name__} is not a manifest downloader")


def _naming() -> dict:
    return {
        "title": context_tracker.title,
        "media_type": context_tracker.media_type,
        "season": int(context_tracker.season or 0),
        "episode": int(context_tracker.episode or 0),
        "episode_name": context_tracker.episode_name,
    }


def _wire_enc_override(override: dict | None) -> dict | None:
    """``hls_enc_override`` carries the AES key as bytes: send it as hex (``build_downloader`` turns it back)."""
    if not override:
        return override
    return {k: (v.hex() if isinstance(v, (bytes, bytearray)) else v) for k, v in override.items()}


def media_from_downloader(downloader, *, key=None) -> tuple[ResolvedMedia, dict]:
    """Snapshot an HLS/DASH/ISM downloader's inputs as ``(media, server-side callables)``; *key* replaces the manual key (``--export`` stores the resolved ones)."""
    kind, url, content, headers = _kind_and_manifest(downloader)
    hooks = {name: getattr(downloader, name, None) for name in SERVER_SIDE_CALLABLES}
    hooks = {name: fn for name, fn in hooks.items() if fn is not None}
    key = key or downloader.key
    media = ResolvedMedia(
        kind=kind,
        url=url,
        manifest_content=content,
        headers=dict(headers or {}),
        cookies=dict(getattr(downloader, "cookies", None) or {}),
        license_url=downloader.license_url,
        license_headers=dict(downloader.license_headers) if downloader.license_headers else None,
        license_data=downloader.license_data,
        license_certificate=downloader.license_certificate,
        key=key,
        drm_preference=downloader.drm_preference,
        has_drm=bool(getattr(downloader, "has_drm", False) or downloader.license_url or key or hooks.get("license_request_fn")),
        license_proxy=bool(hooks.get("license_request_fn")),
        manifest_refresh=bool(hooks.get("manifest_refresh_fn")),
        other_tracks=list(getattr(downloader, "other_tracks", None) or []),
        custom_filters=dict(getattr(downloader, "custom_filters", None) or {}) or None,
        display_selected_only=bool(getattr(downloader, "display_selected_only", False)),
        hls_enc_override=_wire_enc_override(getattr(downloader, "hls_enc_override", None)),
        chapters=getattr(downloader, "chapters", None),
        poster_url=getattr(downloader, "poster_url", None),
        **_naming(),
    )
    return media, hooks


def record_downloader(downloader, buffer: CaptureBuffer) -> None:
    """Snapshot an HLS/DASH/ISM downloader's inputs into *buffer*."""
    try:
        media, hooks = media_from_downloader(downloader)
    except ValueError:
        buffer.fail(f"{context_tracker.site_name or 'service'} uses {type(downloader).__name__}, which is not supported through a remote server")
        return
    except Exception as exc:
        logger.exception("capture failed")
        buffer.fail(f"could not capture {type(downloader).__name__} download: {exc}")
        return
    buffer.add(media, hooks)


def record_post_process(kind: str, args: dict) -> bool:
    """A service ran its post-download step (song tagging/conversion) while capturing: keep it for the client."""
    buffer = context_tracker.capture
    if buffer is None:
        return False
    with buffer._lock:
        if buffer.items:
            buffer.items[-1].post_process = {"kind": kind, "args": args}
    return True


def mp4_media(url: str, headers: dict | None, key, chapters, poster_url) -> ResolvedMedia:
    """A plain MP4 download has no manifest: the URL (plus headers and key) is all there is."""
    return ResolvedMedia(
        kind="mp4",
        url=url,
        headers=dict(headers or {}),
        key=key,
        chapters=chapters,
        poster_url=poster_url or context_tracker.poster_url,
        **_naming(),
    )


def record_mp4(url: str, headers: dict | None, key, chapters, poster_url, buffer: CaptureBuffer) -> None:
    """Plain MP4 downloads have no manifest: the client can fetch the URL directly."""
    buffer.add(mp4_media(url, headers, key, chapters, poster_url))


@contextmanager
def capturing(buffer: CaptureBuffer | None = None, keys: list | None = None):
    """Run the body with the calling thread in capture mode (and restore the previous state afterwards)."""
    previous = (context_tracker.capture, context_tracker.capture_keys)
    context_tracker.capture = buffer
    context_tracker.capture_keys = keys
    try:
        yield buffer if buffer is not None else keys
    finally:
        context_tracker.capture, context_tracker.capture_keys = previous
