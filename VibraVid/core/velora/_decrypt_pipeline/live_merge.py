# 01.04.25

import gzip
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from VibraVid.core.muxing.helper.video import _PNG_SIGNATURE, _resolve_png_segment

from .constants import _LIVE_MERGE_BUFSIZE, logger


class _LiveMerger:
    def __init__(self, out_path: Path, expected_order: list, on_chunk: Callable[[bytes], None] | None = None):
        """
        Args:
            - out_path: Path to the final merged output file.
            - expected_order: List of keys in the order they should be merged.
            - on_chunk: Optional callback invoked with each chunk of data written to the output file.
        """
        self._expected = expected_order
        self._cursor = 0
        self._pending: dict[Any, Path] = {}
        self._lock = threading.Lock()
        self._fh = open(out_path, "wb")
        self._failed = False
        self._adopted = False
        self._on_chunk = on_chunk
        self._png_wrapped = 0
        self._gzip_count = 0

    def submit(self, key: Any, path: Path) -> None:
        with self._lock:
            if self._failed or self._adopted:
                return
            self._pending[key] = path
            while self._cursor < len(self._expected) and self._expected[self._cursor] in self._pending:
                ready_key = self._expected[self._cursor]
                ready_path = self._pending.pop(ready_key)
                try:
                    with open(ready_path, "rb") as src:
                        head = src.read(8)
                        if head[:2] == b"\x1f\x8b":
                            # gzip-compressed segment: mirrors the same check in
                            # binary_merge_segments()'s serial/parallel merge -- some
                            # CDNs ship segments gzip'd instead of as raw MPEG-TS.
                            try:
                                payload = gzip.decompress(head + src.read())
                                self._gzip_count += 1
                            except Exception as exc:
                                logger.warning(f"[live_merge] failed to decompress gzip segment {ready_path.name}: {exc}, using raw data")
                                src.seek(0)
                                payload = src.read()
                            self._fh.write(payload)
                            if self._on_chunk is not None:
                                self._on_chunk(payload)
                        elif head == _PNG_SIGNATURE:
                            # Some CDNs disguise the real MPEG-TS payload as a PNG image
                            # (either a fake header or a genuinely valid stego-PNG) -- see
                            # _resolve_png_segment. This path only knows raw bytes, unlike
                            # binary_merge_segments(), so it must resolve the wrapper here too.
                            payload, kind = _resolve_png_segment(head + src.read())
                            if kind != "raw":
                                self._png_wrapped += 1
                            else:
                                logger.warning(f"[live_merge] PNG-wrapped segment {ready_path.name} has no TS sync, using raw data")
                            self._fh.write(payload)
                            if self._on_chunk is not None:
                                self._on_chunk(payload)
                        elif self._on_chunk is None:
                            self._fh.write(head)
                            shutil.copyfileobj(src, self._fh, _LIVE_MERGE_BUFSIZE)
                        else:
                            self._fh.write(head)
                            self._on_chunk(head)
                            while True:
                                chunk = src.read(_LIVE_MERGE_BUFSIZE)
                                if not chunk:
                                    break
                                self._fh.write(chunk)
                                self._on_chunk(chunk)
                except Exception as exc:
                    self._failed = True
                    logger.warning(f"[live_merge] failed appending segment {ready_key!r} ({ready_path.name}): {exc}")
                    return
                self._cursor += 1

    @property
    def out_path(self) -> Path:
        return Path(self._fh.name)

    def can_adopt(self) -> bool:
        with self._lock:
            return self._on_chunk is None and not self._failed and not self._adopted

    def adopt(self, complete_path: Path) -> bool:
        """Adopt an already-complete file as the final output, replacing the current out_path. Returns True if successful, False if adoption was not possible (e.g. because a chunk callback is active or the merger has failed)."""
        with self._lock:
            if self._on_chunk is not None or self._failed or self._adopted:
                return False
            out_path = self.out_path
            self._fh.close()
            try:
                complete_path.replace(out_path)
            except OSError as exc:
                self._failed = True
                logger.warning(f"[live_merge] adopt failed for {complete_path.name}: {exc}")
                return False
            self._adopted = True
            self._cursor = len(self._expected)
            self._pending.clear()
            return True

    def attach_feeder(self, feed_fn: Callable[[bytes], None]) -> None:
        """Retroactively wire a streaming-mux feeder into an already-running live merge"""
        with self._lock:
            if self._on_chunk is not None or self._failed or self._adopted:
                return
            self._fh.flush()
            with open(self._fh.name, "rb") as src:
                while True:
                    chunk = src.read(_LIVE_MERGE_BUFSIZE)
                    if not chunk:
                        break
                    feed_fn(chunk)
            self._on_chunk = feed_fn

    @property
    def complete(self) -> bool:
        with self._lock:
            return not self._failed and self._cursor == len(self._expected)

    @property
    def failed(self) -> bool:
        with self._lock:
            return self._failed

    def close(self) -> None:
        if self._adopted:
            return

        try:
            self._fh.close()
        except Exception:
            pass
