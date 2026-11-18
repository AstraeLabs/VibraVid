# 01.04.25

import gzip
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context import _SegmentDownloadContext


def _sniff_vtt_content(raw: bytes) -> bool:
    """Prova a leggere i primi byte come testo; se sono gzip, decomprime prima."""
    try:
        if raw[:2] == b"\x1f\x8b":  # magic number gzip
            raw = gzip.decompress(raw)[:64]
        else:
            raw = raw[:64]
        head = raw.decode("utf-8-sig", errors="replace").lstrip("﻿�").lstrip()
        return head.startswith("WEBVTT")
    except Exception:
        return False


class DownloadEventMixin:
    def _handle_download_event(self, ctx: "_SegmentDownloadContext", event: dict[str, Any]) -> None:
        event_name = (event.get("event") or "").lower()
        if event_name == "error":
            msg = event.get("message") or event.get("error")
            if msg:
                ctx.seg_errors.append(str(msg))
            return

        if event_name in {"start", "summary", "retry", "cancelled", "progress"}:
            return

        path_value = event.get("path")
        if not path_value:
            return

        # A "skipped" event means the segment file already existed on disk
        # (resume) -- it still needs to go through the decrypt worker,
        # since a leftover file from an interrupted/failed run may never
        # have been decrypted (see _reads_as_plaintext_ts / _reads_as_self_initializing_mp4
        # guards in the per-protocol decrypt functions for the idempotency check).
        if ctx.decrypt_threads:
            ctx.decrypt_queue.put(dict(event))
