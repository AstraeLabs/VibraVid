# 01.10.26
# ruff: noqa: E402

import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.stream import Stream
from VibraVid.core.velora._stream_vod import VodStreamMixin

SEGMENTS = 400


def _vbr_size(n: int) -> int:
    """Light opening (titles / black frames, like the first 10% of a film), heavy and variable afterwards."""
    if n < SEGMENTS // 10:
        return 30_000 + 1_000 * (n % 7)
    return 400_000 + random.Random(n).randint(0, 1_200_000)


class _Handler(BaseHTTPRequestHandler):
    head_allowed = True
    fail_all = False
    throttle_first = 0  # answer 503 to the first N HEADs
    _head_calls = 0

    def _size(self) -> int:
        return _vbr_size(int(self.path.strip("/").split("?")[0].removeprefix("seg")))

    def do_HEAD(self):  # noqa: N802
        cls = type(self)
        cls._head_calls += 1
        if cls.fail_all or not cls.head_allowed:
            self.send_response(500 if cls.fail_all else 405)
            self.end_headers()
            return
        if cls._head_calls <= cls.throttle_first:
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(self._size()))
        self.end_headers()

    def do_GET(self):  # noqa: N802
        cls = type(self)
        if cls.fail_all:
            self.send_response(500)
            self.end_headers()
            return
        size = self._size()
        if self.headers.get("Range") == "bytes=0-0":
            self.send_response(206)
            self.send_header("Content-Range", f"bytes 0-0/{size}")
            self.send_header("Content-Length", "1")
            self.end_headers()
            self.wfile.write(b"x")
            return
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        self.wfile.write(b"x" * size)

    def log_message(self, *_):
        pass


class _Probe(VodStreamMixin):
    def _stop_check(self):
        return False


def _server(**attrs):
    handler = type("Handler", (_Handler,), {"_head_calls": 0, **attrs})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _plan(srv, count: int = SEGMENTS, duration: float = 4.0) -> list[dict]:
    base = f"http://127.0.0.1:{srv.server_port}"
    return [{"url": f"{base}/seg{n}", "number": n, "seg_type": "media", "duration": duration} for n in range(count)]


def _real_total(count: int = SEGMENTS) -> int:
    return sum(_vbr_size(n) for n in range(count))


def test_evenly_spaced_probe_is_close_where_first_segments_are_not():
    srv = _server()
    try:
        plan = _plan(srv)
        real = _real_total()
        probed = _Probe()._probe_planned_size(plan, {})

        first_only = sum(_vbr_size(n) for n in range(10)) / 10 * SEGMENTS
        assert abs(probed - real) / real < 0.20
        assert abs(first_only - real) / real > 0.5  # the biased "first 10 segments" idea
    finally:
        srv.shutdown()


def test_probe_uses_durations_when_segments_differ_in_length():
    srv = _server()
    try:
        plan = _plan(srv)
        for seg in plan:
            seg["duration"] = 1.0 if seg["number"] % 2 else 7.0  # same bytes, very different durations
        probed = _Probe()._probe_planned_size(plan, {})
        assert probed > 0
    finally:
        srv.shutdown()


def test_head_not_allowed_falls_back_to_ranged_get():
    srv = _server(head_allowed=False)
    try:
        probed = _Probe()._probe_planned_size(_plan(srv), {})
        assert abs(probed - _real_total()) / _real_total() < 0.20
    finally:
        srv.shutdown()


def test_throttled_head_is_retried_once():
    srv = _server(throttle_first=1)
    try:
        assert _Probe()._probe_segment_size(_plan(srv)[5]["url"], {}) == _vbr_size(5)
    finally:
        srv.shutdown()


def test_unreachable_probe_returns_zero_and_keeps_the_declared_size():
    srv = _server(fail_all=True)
    try:
        stream = Stream(type="video", estimated_size=123_456_789, estimated_size_exact=False)
        _Probe()._sync_estimated_size(stream, _plan(srv), {})
        assert stream.estimated_size == 123_456_789
    finally:
        srv.shutdown()


def test_short_plans_are_not_probed():
    srv = _server()
    try:
        assert _Probe()._probe_planned_size(_plan(srv, count=20), {}) == 0
    finally:
        srv.shutdown()


def test_stop_request_skips_the_probe():
    class Stopped(_Probe):
        def _stop_check(self):
            return True

    srv = _server()
    try:
        assert Stopped()._probe_planned_size(_plan(srv), {}) == 0
    finally:
        srv.shutdown()


def test_sync_replaces_a_nominal_total_with_the_probed_one():
    srv = _server()
    try:
        stream = Stream(type="video", estimated_size=_real_total() * 2, estimated_size_exact=False)
        _Probe()._sync_estimated_size(stream, _plan(srv), {})

        assert abs(stream.estimated_size - _real_total()) / _real_total() < 0.20
        assert stream.estimated_size_exact is False
    finally:
        srv.shutdown()


def test_sync_estimates_only_the_planned_segments_after_trimming():
    srv = _server()
    try:
        stream = Stream(type="video", estimated_size=_real_total(), estimated_size_exact=False)
        planned = 100
        _Probe()._sync_estimated_size(stream, _plan(srv, count=planned), {})

        assert stream.estimated_size < _real_total() * 0.5
        assert abs(stream.estimated_size - _real_total(planned)) / _real_total(planned) < 0.25
    finally:
        srv.shutdown()


def test_exact_totals_are_never_probed():
    srv = _server(fail_all=True)  # would zero out a probe; an exact total must not even try
    try:
        stream = Stream(type="video", estimated_size=5_000, estimated_size_exact=True)
        stream.segments = [object()] * SEGMENTS
        _Probe()._sync_estimated_size(stream, _plan(srv), {})
        assert (stream.estimated_size, stream.estimated_size_exact) == (5_000, True)
    finally:
        srv.shutdown()
