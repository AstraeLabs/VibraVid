# 26.09.26
# ruff: noqa: E402

import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.velora._decrypt_pipeline import _run_curl_cffi_fallback

SEGMENT_BODY = b"segment-data"


class _DelayedHandler(BaseHTTPRequestHandler):
    delay_seconds = 0.12
    fail_paths: set[str] = set()

    def do_GET(self):  # noqa: N802
        time.sleep(self.delay_seconds)
        if self.path in self.fail_paths:
            self.send_response(500)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(SEGMENT_BODY)))
        self.end_headers()
        self.wfile.write(SEGMENT_BODY)

    def log_message(self, format, *args):  # noqa: A002
        pass


def _start_server(delay_seconds: float = 0.12, fail_paths: set[str] | None = None):
    handler = type("Handler", (_DelayedHandler,), {"delay_seconds": delay_seconds, "fail_paths": fail_paths or set()})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _make_tasks(base_url: str, out_dir: Path, count: int) -> list[dict]:
    tasks = []
    for n in range(count):
        tasks.append(
            {
                "url": f"{base_url}/seg{n:05d}",
                "path": str(out_dir / f"seg_{n:05d}.ts"),
                "headers": {},
                "task_key": "test",
                "label": "test",
                "display_label": "test",
            }
        )
    return tasks


def test_stop_check_interrupts_in_flight_fallback():
    """Cancellation set mid-flight must stop the fallback before all tasks complete (issue #735)."""
    server, thread = _start_server(delay_seconds=0.12)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            tasks = _make_tasks(base_url, out_dir, count=16)
            plan = {"headers": {}, "retry_count": 0, "timeout_seconds": 5.0, "concurrency": 2}

            stop_event = threading.Event()

            def _stop_after():
                time.sleep(0.25)
                stop_event.set()

            threading.Thread(target=_stop_after, daemon=True).start()

            recovered = _run_curl_cffi_fallback(
                tasks,
                plan,
                done_before=0,
                total=16,
                progress_cb=None,
                event_cb=None,
                stop_check=stop_event.is_set,
            )

            assert len(recovered) < 16, (
                f"expected cancellation to stop the fallback before all 16 segments completed, "
                f"but got {len(recovered)}/16"
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_progress_offset_never_goes_backwards():
    """progress_cb must be called, offset by segments already done, and never regress the running total (issue #735)."""
    server, thread = _start_server(delay_seconds=0.01)
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            fallback_count = 16
            done_before = 5
            total = done_before + fallback_count
            tasks = _make_tasks(base_url, out_dir, count=fallback_count)
            plan = {"headers": {}, "retry_count": 0, "timeout_seconds": 5.0, "concurrency": 4}

            progress_calls: list[tuple[int, int, int, float]] = []

            def _progress_cb(done, total_, total_bytes, speed):
                progress_calls.append((done, total_, total_bytes, speed))

            recovered = _run_curl_cffi_fallback(
                tasks,
                plan,
                done_before=done_before,
                total=total,
                progress_cb=_progress_cb,
                event_cb=None,
                stop_check=None,
            )

            assert len(recovered) == fallback_count
            assert progress_calls, "progress_cb was never called during the fallback"

            seen_done = [call[0] for call in progress_calls]
            assert all(done >= done_before for done in seen_done), (
                f"progress reported below done_before={done_before}: {seen_done}"
            )
            assert seen_done == sorted(seen_done), f"progress went backwards: {seen_done}"
            assert seen_done[-1] == done_before + fallback_count

            assert all(call[1] == total for call in progress_calls), (
                "progress_cb must always report the whole-stream total, not the fallback-local total"
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_errors_are_forwarded_to_event_cb():
    """Failed fallback segments must reach event_cb as error events instead of being silently dropped (issue #735)."""
    fail_path = "/seg00003"
    server, thread = _start_server(delay_seconds=0.01, fail_paths={fail_path})
    try:
        base_url = f"http://127.0.0.1:{server.server_port}"
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            tasks = _make_tasks(base_url, out_dir, count=6)
            plan = {"headers": {}, "retry_count": 0, "timeout_seconds": 5.0, "concurrency": 4}

            events: list[dict] = []

            recovered = _run_curl_cffi_fallback(
                tasks,
                plan,
                done_before=0,
                total=6,
                progress_cb=None,
                event_cb=events.append,
                stop_check=None,
            )

            assert len(recovered) == 5, "the one failing segment must not be counted as recovered"
            error_events = [e for e in events if e.get("event") == "error"]
            assert error_events, "the failed segment's error event must reach event_cb, not be dropped"
            assert any(fail_path in (e.get("url") or "") for e in error_events)
    finally:
        server.shutdown()
        thread.join(timeout=5)


if __name__ == "__main__":
    test_stop_check_interrupts_in_flight_fallback()
    test_progress_offset_never_goes_backwards()
    test_errors_are_forwarded_to_event_cb()
    print("All tests passed.")
