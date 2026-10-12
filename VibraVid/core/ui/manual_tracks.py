"""Thread-safe, one-shot manual track selection for GUI download workers."""

import threading
import time
import uuid


class TrackSelectionError(RuntimeError):
    """Cancelled, expired or invalid interactive track selection."""


def _as_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _describe(stream, index):
    """Expose track metadata only, never URLs, cookies or DRM secrets."""
    return {
        "index": index,
        "type": str(getattr(stream, "type", "") or ""),
        "video_range": str(getattr(stream, "video_range", "") or ""),
        "default": bool(getattr(stream, "default", False)),
        "resolution": str(getattr(stream, "resolution", "") or ""),
        "width": _as_int(getattr(stream, "width", 0)),
        "height": _as_int(getattr(stream, "height", 0)),
        "codecs": str(getattr(stream, "codecs", "") or ""),
        "language": str(getattr(stream, "resolved_language", "") or getattr(stream, "language", "") or ""),
        "bitrate": _as_int(getattr(stream, "bitrate", 0)),
        "channels": str(getattr(stream, "channels", "") or ""),
        "forced": bool(getattr(stream, "forced", False)),
        "sdh": bool(getattr(stream, "is_sdh", False)),
        "cc": bool(getattr(stream, "is_cc", False)),
        "selected": bool(getattr(stream, "selected", False)),
    }


class _Pending:
    def __init__(self, download_id, streams):
        self.download_id = download_id
        self.token = uuid.uuid4().hex
        self.tracks = [_describe(stream, i) for i, stream in enumerate(streams)]
        self.event = threading.Event()
        self.selected = None


def _catalog_key(tracks):
    """Match a season's track catalogue without relying on manifest URLs.

    Segment bitrates may differ between episodes. When two tracks have the
    same other attributes, include bitrate to avoid ambiguous reuse.
    """
    keys = [
        (track["type"], track["resolution"], track["width"], track["height"],
         track["codecs"], track["video_range"], track["language"], track["channels"],
         track["default"], track["forced"], track["sdh"], track["cc"])
        for track in tracks
    ]
    if len(keys) != len(set(keys)):
        return tuple(key + (track["bitrate"],) for key, track in zip(keys, tracks, strict=True))
    return tuple(keys)


class ManualTrackBroker:
    def __init__(self):
        self._lock = threading.RLock()
        self._requests = {}
        self._remembered = {}

    def forget(self, download_id):
        """Discard the remembered choice when a job completes or is cancelled."""
        with self._lock:
            self._remembered.pop(download_id, None)

    def list_pending(self):
        with self._lock:
            return [
                {"download_id": req.download_id, "token": req.token, "tracks": req.tracks}
                for req in self._requests.values()
            ]

    def submit(self, download_id, token, selected):
        with self._lock:
            req = self._requests.get(download_id)
            if req is None or req.token != token or req.event.is_set():
                raise TrackSelectionError("Selection expired or unavailable")
            if not isinstance(selected, list) or not selected:
                raise TrackSelectionError("Select at least one track")
            if any(type(i) is not int or i < 0 or i >= len(req.tracks) for i in selected):
                raise TrackSelectionError("Invalid track index")
            if len(selected) != len(set(selected)):
                raise TrackSelectionError("Duplicate track index")
            req.selected = set(selected)
            req.event.set()

    def choose(self, download_id, streams, cancelled=None, timeout=900,
               on_wait=None, on_confirm=None):
        if not download_id or not streams:
            raise TrackSelectionError("No tracks available for manual selection")
        if cancelled is not None and cancelled():
            raise TrackSelectionError("Track selection cancelled")
        req = _Pending(download_id, streams)
        key = _catalog_key(req.tracks)
        with self._lock:
            if download_id in self._requests:
                raise TrackSelectionError("Another track selection is already pending")
            remembered = self._remembered.get(download_id)
            if remembered is not None and remembered[0] == key:
                selected = remembered[1]
                for index, stream in enumerate(streams):
                    stream.selected = index in selected
                return set(selected)
            self._requests[download_id] = req
        try:
            # The worker releases its concurrency slot only when waiting for
            # a human, not when reusing an existing season selection.
            if on_wait is not None:
                on_wait()
            deadline = time.monotonic() + timeout
            while not req.event.wait(0.25):
                if cancelled is not None and cancelled():
                    raise TrackSelectionError("Track selection cancelled")
                if time.monotonic() >= deadline:
                    raise TrackSelectionError("Track selection timed out")
            if cancelled is not None and cancelled():
                raise TrackSelectionError("Track selection cancelled")
            selected = set(req.selected)
            for index, stream in enumerate(streams):
                stream.selected = index in selected
            with self._lock:
                self._remembered[download_id] = (key, selected)
                if self._requests.get(download_id) is req:
                    del self._requests[download_id]
            # Slot reacquisition may wait for an already-running job.
            if on_confirm is not None:
                on_confirm()
            return selected
        finally:
            with self._lock:
                if self._requests.get(download_id) is req:
                    del self._requests[download_id]


manual_track_broker = ManualTrackBroker()
