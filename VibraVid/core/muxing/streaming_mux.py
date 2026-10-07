# 11.09.26

import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_FEED_QUEUE_MAXSIZE = 64
_PIPE_CONNECT_TIMEOUT = 60.0
_SENTINEL = object()


class ChunkRelay:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: list[bytes] = []
        self._feeder: Callable[[bytes], None] | None = None
        self._closed = False
        self._on_close: Callable[[], None] | None = None

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if self._closed:
                return
            if self._feeder is None:
                self._buffer.append(chunk)
                return
            fn = self._feeder
        fn(chunk)

    def attach(self, feeder: Callable[[bytes], None]) -> bool:
        with self._lock:
            if self._closed:
                return False
            buffered, self._buffer = self._buffer, []
            self._feeder = feeder
        for chunk in buffered:
            feeder(chunk)
        return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._buffer = []
            hook = self._on_close
        if hook is not None:
            hook()

    def on_close(self, hook: Callable[[], None]) -> None:
        with self._lock:
            if self._closed:
                fire = True
            else:
                self._on_close = hook
                fire = False
        if fire:
            hook()


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

    @staticmethod
    def _join_slice(thread: threading.Thread, timeout: float, poll: float = 0.25) -> bool:
        """Join in short slices so Ctrl+C stays deliverable; True when finished in time."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if not thread.is_alive():
                return True
            if time.monotonic() >= deadline:
                return False
            thread.join(timeout=min(poll, max(0.05, deadline - time.monotonic())))

    def _wait_proc_interruptible(self, timeout: float) -> int | None:
        """Wait on ffmpeg in 0.5s slices so abort()/Ctrl+C takes effect promptly."""
        deadline = time.monotonic() + max(0.0, timeout)
        assert self._proc is not None
        while True:
            if self._failed.is_set() and self._proc.poll() is not None:
                return self._proc.returncode
            try:
                return self._proc.wait(timeout=min(0.5, max(0.05, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                if self._failed.is_set() or time.monotonic() >= deadline:
                    try:
                        self._proc.kill()
                    except OSError:
                        pass
                    try:
                        return self._proc.wait(timeout=10)
                    except Exception:
                        return self._proc.returncode
                continue

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

        try:
            self._feed_queue.put(_SENTINEL, timeout=5.0)
        except queue.Full:
            self._mark_failed("feed queue full on finish — ffmpeg stalled")
        if self._feeder_thread:
            self._join_slice(self._feeder_thread, timeout=self._write_timeout)

        returncode = self._wait_proc_interruptible(self._write_timeout)

        if self._stderr_thread:
            self._stderr_thread.join(timeout=2.0)

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

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes as _wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _k32.CreateNamedPipeW.restype = ctypes.c_void_p
    _k32.CreateNamedPipeW.argtypes = [
        ctypes.c_wchar_p, _wt.DWORD, _wt.DWORD, _wt.DWORD,
        _wt.DWORD, _wt.DWORD, _wt.DWORD, ctypes.c_void_p,
    ]
    _k32.ConnectNamedPipe.restype = _wt.BOOL
    _k32.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _k32.WriteFile.restype = _wt.BOOL
    _k32.WriteFile.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, _wt.DWORD,
        ctypes.POINTER(_wt.DWORD), ctypes.c_void_p,
    ]
    _k32.FlushFileBuffers.restype = _wt.BOOL
    _k32.FlushFileBuffers.argtypes = [ctypes.c_void_p]
    _k32.DisconnectNamedPipe.restype = _wt.BOOL
    _k32.DisconnectNamedPipe.argtypes = [ctypes.c_void_p]
    _k32.CloseHandle.restype = _wt.BOOL
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]

    _PIPE_ACCESS_OUTBOUND = 0x00000002
    _FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
    _PIPE_TYPE_BYTE = 0x00000000
    _PIPE_WAIT = 0x00000000
    _ERROR_PIPE_CONNECTED = 535

    def _win_make_pipe(name: str):
        h = _k32.CreateNamedPipeW(
            name,
            _PIPE_ACCESS_OUTBOUND | _FILE_FLAG_FIRST_PIPE_INSTANCE,
            _PIPE_TYPE_BYTE | _PIPE_WAIT,
            1, 4 << 20, 0, 0, None,
        )
        if h in (None, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            raise OSError(f"CreateNamedPipeW({name!r}) failed: error {ctypes.get_last_error()}")
        return h

    def _win_connect(h) -> bool:
        if _k32.ConnectNamedPipe(h, None):
            return True
        return ctypes.get_last_error() == _ERROR_PIPE_CONNECTED

    def _win_write(h, data: bytes) -> None:
        n = _wt.DWORD(0)
        if not _k32.WriteFile(h, data, len(data), ctypes.byref(n), None):
            raise OSError(f"WriteFile failed: error {ctypes.get_last_error()}")

    def _win_close(h, graceful: bool = True) -> None:
        try:
            if graceful:
                _k32.FlushFileBuffers(h)
            _k32.DisconnectNamedPipe(h)
        except Exception:
            pass
        _k32.CloseHandle(h)


class _NamedPipe:
    def __init__(self, path: str) -> None:
        self.path = path
        self._closed = False
        self._close_lock = threading.Lock()
        if sys.platform == "win32":
            self._h = _win_make_pipe(path)  # type: ignore[name-defined]
            self._fh = None
        else:
            os.mkfifo(path)
            self._fh = None

    def connect(self) -> bool:
        """Block until ffmpeg opens the read end. Returns False on error or cancel."""
        try:
            if sys.platform == "win32":
                return _win_connect(self._h)  # type: ignore[name-defined]
            else:
                import errno as _errno
                deadline = time.monotonic() + _PIPE_CONNECT_TIMEOUT
                while time.monotonic() < deadline:
                    if self._closed:
                        return False
                    try:
                        fd = os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)
                        import io
                        self._fh = io.FileIO(fd, mode="wb", closefd=True)
                        return True
                    except OSError as exc:
                        if exc.errno in (_errno.ENXIO, _errno.EINTR):
                            time.sleep(0.05)
                            continue
                        return False
                return False
        except OSError:
            return False

    def write(self, data: bytes) -> None:
        if sys.platform == "win32":
            _win_write(self._h, data)  # type: ignore[name-defined]
        else:
            assert self._fh is not None
            self._fh.write(data)

    def close(self, graceful: bool = True) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if sys.platform == "win32":
            try:
                _win_close(self._h, graceful=graceful)  # type: ignore[name-defined]
            except OSError:
                pass
        else:
            try:
                if self._fh is not None:
                    self._fh.close()
            except OSError:
                pass


def _drain_queue(q: queue.Queue) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass


class NamedPipeMuxer:
    def __init__(self, n_pipes: int, write_timeout: float = 120.0) -> None:
        self._n = n_pipes
        self._write_timeout = write_timeout
        self._failed = threading.Event()
        self._fail_reason = ""
        self._lock = threading.Lock()
        self._ffmpeg_started_event = threading.Event()

        session = uuid.uuid4().hex[:8]
        if sys.platform == "win32":
            self._pipe_paths = [rf"\\.\pipe\vv_{session}_{i}" for i in range(n_pipes)]
            self._tmpdir: str | None = None
        else:
            self._tmpdir = tempfile.mkdtemp(prefix="vv_pipe_")
            self._pipe_paths = [os.path.join(self._tmpdir, f"p{i}") for i in range(n_pipes)]

        self._queues: list[queue.Queue] = [queue.Queue(maxsize=64) for _ in range(n_pipes)]
        self._pipes: list[_NamedPipe] = []
        self._connect_done: list[threading.Event] = [threading.Event() for _ in range(n_pipes)]
        self._connect_ok: list[bool] = [False] * n_pipes
        self._proc: subprocess.Popen | None = None
        self._stderr_chunks: list[bytes] = []
        self._stderr_thread: threading.Thread | None = None
        self._writer_threads: list[threading.Thread] = []

    @property
    def pipe_paths(self) -> list[str]:
        return list(self._pipe_paths)

    def prepare(self) -> None:
        """Create named pipe servers and start per-pipe writer threads."""
        if self._pipes:
            raise RuntimeError("already prepared")
        self._pipes = [_NamedPipe(p) for p in self._pipe_paths]
        for i in range(self._n):
            t = threading.Thread(target=self._pipe_writer, args=(i,), daemon=True)
            self._writer_threads.append(t)
            t.start()

    def start_ffmpeg(self, ffmpeg_cmd: list[str]) -> None:
        """Launch the ffmpeg subprocess.  Writer threads are already waiting for it."""
        if self._failed.is_set():
            return  # abort() was called before we got here
        self._proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        def _drain_stderr() -> None:
            assert self._proc and self._proc.stderr
            for line in self._proc.stderr:
                with self._lock:
                    self._stderr_chunks.append(line)
                    if len(self._stderr_chunks) > 500:
                        self._stderr_chunks.pop(0)

        self._stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        self._stderr_thread.start()
        self._ffmpeg_started_event.set()

    def start(self, ffmpeg_cmd: list[str]) -> None:
        """Create pipe servers, launch ffmpeg, start per-pipe writer threads."""
        self.prepare()
        self.start_ffmpeg(ffmpeg_cmd)

    def _pipe_writer(self, i: int) -> None:
        pipe = self._pipes[i]
        q = self._queues[i]

        connected = pipe.connect()
        self._connect_ok[i] = bool(connected)
        self._connect_done[i].set()
        if not connected:
            self._mark_failed(f"pipe {i}: connect timed out or cancelled")
            _drain_queue(q)
            return

        deadline = time.monotonic() + 7200.0
        while True:
            if self._failed.is_set():
                _drain_queue(q)
                break
            try:
                item = q.get(timeout=0.5)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    self._mark_failed(f"pipe {i}: no data for 2 h — giving up")
                    break
                continue
            if item is _SENTINEL:
                break
            try:
                pipe.write(item)
            except OSError as exc:
                self._mark_failed(f"pipe {i}: write error: {exc}")
                _drain_queue(q)
                break

        pipe.close(graceful=not self._failed.is_set())

    def get_writer(self, n: int) -> Callable[[bytes], None]:
        """Return a callable that blocks until space is available in pipe *n*'s queue."""
        q = self._queues[n]
        failed = self._failed

        def _write(data: bytes) -> None:
            while True:
                if failed.is_set():
                    return
                try:
                    q.put(data, timeout=5.0)
                    return
                except queue.Full:
                    pass  # ffmpeg is slow — keep waiting (backpressure)

        return _write

    def close_writer(self, n: int) -> None:
        """Signal EOF for track *n* (no more data will be written)."""
        while True:
            if self._failed.is_set():
                return
            try:
                self._queues[n].put(_SENTINEL, timeout=5.0)
                return
            except queue.Full:
                pass  # ffmpeg is slow — keep retrying

    def finish(self) -> StreamingMuxResult:
        # In deferred mode (prepare() called, start_ffmpeg() runs in a background
        # thread), wait up to 5 min for ffmpeg to start (or for abort() to fire).
        # Poll so abort()/Ctrl+C unblocks finish() instead of sleeping 5 min.
        _start_deadline = time.monotonic() + 300.0
        while not self._ffmpeg_started_event.is_set():
            if self._failed.is_set() or time.monotonic() >= _start_deadline:
                break
            self._ffmpeg_started_event.wait(timeout=0.5)
        if not self._ffmpeg_started_event.is_set() and not self._failed.is_set():
            self._mark_failed("ffmpeg never started — audio timed out")
        if self._proc is None:
            self._cleanup()
            return StreamingMuxResult(ok=False, returncode=None, error=self._fail_reason or "never started")

        for t in self._writer_threads:
            StreamingMuxFeeder._join_slice(t, timeout=self._write_timeout)

        rc: int | None
        try:
            _wait_deadline = time.monotonic() + max(0.0, self._write_timeout)
            while True:
                try:
                    rc = self._proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if self._failed.is_set() or time.monotonic() >= _wait_deadline:
                        try:
                            self._proc.kill()
                        except OSError:
                            pass
                        try:
                            rc = self._proc.wait(timeout=10)
                        except Exception:
                            rc = self._proc.returncode
                        break
                    continue
        except Exception:
            rc = self._proc.returncode

        if self._stderr_thread:
            self._stderr_thread.join(timeout=2.0)

        with self._lock:
            stderr_tail = b"".join(self._stderr_chunks).decode(errors="replace")[-4000:]

        self._cleanup()

        if self._failed.is_set():
            return StreamingMuxResult(ok=False, returncode=rc, stderr_tail=stderr_tail, error=self._fail_reason)
        if rc != 0:
            return StreamingMuxResult(ok=False, returncode=rc, stderr_tail=stderr_tail, error=f"ffmpeg exited {rc}")
        return StreamingMuxResult(ok=True, returncode=rc, stderr_tail=stderr_tail)

    def abort(self) -> None:
        self._mark_failed("aborted")
        self._ffmpeg_started_event.set()  # unblock finish() in deferred mode
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
        
        for i, p in enumerate(self._pipes):
            done = self._connect_done[i] if i < len(self._connect_done) else None
            if done is not None and not done.is_set():
                continue
            try:
                p.close(graceful=False)
            except Exception:
                pass
        
        for q in self._queues:
            _drain_queue(q)

    def _mark_failed(self, reason: str) -> None:
        if not self._failed.is_set():
            self._fail_reason = reason
            self._failed.set()

    def _cleanup(self) -> None:
        if self._tmpdir:
            for p in self._pipe_paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            try:
                os.rmdir(self._tmpdir)
            except OSError:
                pass
