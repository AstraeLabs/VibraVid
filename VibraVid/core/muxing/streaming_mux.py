# 11.09.26

import logging
import queue
import subprocess
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_FEED_QUEUE_MAXSIZE = 64
_SENTINEL = object()


@dataclass
class StreamingMuxResult:
    ok: bool
    returncode: int | None
    stderr_tail: str = ""
    error: str = ""


class StreamingMuxFeeder:
    def __init__(self, ffmpeg_cmd: list[str], write_timeout: float = 120.0):
        self._cmd = ffmpeg_cmd
        self._write_timeout = write_timeout
        self._proc: subprocess.Popen | None = None
        self._feed_queue: queue.Queue = queue.Queue(maxsize=_FEED_QUEUE_MAXSIZE)
        self._feeder_thread: threading.Thread | None = None
        self._stderr_chunks: list[bytes] = []
        self._stderr_thread: threading.Thread | None = None
        self._failed = threading.Event()
        self._fail_reason = ""
        self._lock = threading.Lock()

    def start(self) -> None:
        self._proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        def _drain_stderr() -> None:
            assert self._proc is not None and self._proc.stderr is not None
            for line in self._proc.stderr:
                with self._lock:
                    self._stderr_chunks.append(line)
                    if len(self._stderr_chunks) > 500:
                        self._stderr_chunks.pop(0)

        self._stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        self._stderr_thread.start()

        def _feed_loop() -> None:
            assert self._proc is not None and self._proc.stdin is not None
            while True:
                item = self._feed_queue.get()
                if item is _SENTINEL:
                    break
                try:
                    self._proc.stdin.write(item)
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    self._mark_failed(f"stdin write failed: {exc}")
                    break

            try:
                self._proc.stdin.close()
            except OSError:
                pass

        self._feeder_thread = threading.Thread(target=_feed_loop, daemon=True)
        self._feeder_thread.start()

    def _mark_failed(self, reason: str) -> None:
        if not self._failed.is_set():
            self._fail_reason = reason
            self._failed.set()
        
        # Drain any further feed() calls so producers relying on backpressure don't deadlock.
        try:
            while True:
                self._feed_queue.get_nowait()
        except queue.Empty:
            pass

    @property
    def failed(self) -> bool:
        return self._failed.is_set()

    def feed(self, data: bytes) -> bool:
        """Queue *data* for ffmpeg's stdin. Returns False if the session has already failed."""
        if self._failed.is_set():
            return False
        try:
            self._feed_queue.put(data, timeout=self._write_timeout)
        except queue.Full:
            self._mark_failed("feed queue full — ffmpeg stalled")
            return False
        return True

    def finish(self) -> StreamingMuxResult:
        if self._proc is None:
            return StreamingMuxResult(ok=False, returncode=None, error="never started")

        self._feed_queue.put(_SENTINEL)
        if self._feeder_thread:
            self._feeder_thread.join(timeout=self._write_timeout)

        try:
            returncode = self._proc.wait(timeout=self._write_timeout)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=10)
            returncode = self._proc.returncode

        if self._stderr_thread:
            self._stderr_thread.join(timeout=10)

        with self._lock:
            stderr_tail = b"".join(self._stderr_chunks).decode(errors="replace")[-4000:]

        if self._failed.is_set():
            return StreamingMuxResult(ok=False, returncode=returncode, stderr_tail=stderr_tail, error=self._fail_reason)

        if returncode != 0:
            return StreamingMuxResult(ok=False, returncode=returncode, stderr_tail=stderr_tail, error=f"ffmpeg exited {returncode}")

        return StreamingMuxResult(ok=True, returncode=returncode, stderr_tail=stderr_tail)

    def abort(self) -> None:
        """Kill the ffmpeg process immediately (e.g. the download itself failed)."""
        self._mark_failed("aborted by caller")
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
