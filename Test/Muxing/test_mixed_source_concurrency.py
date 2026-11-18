# 07.10.26

import threading
from types import SimpleNamespace

import pytest

from VibraVid.core.downloader import _generic
from VibraVid.core.downloader._generic import Generic_Downloader


class _FakeBarManager:
    def __init__(self, download_id):
        self.download_id = download_id

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def add_prebuilt_tasks(self, prebuilt_tasks):
        pass

    def finish_all_tasks(self):
        pass


@pytest.fixture(autouse=True)
def _fake_bar(monkeypatch):
    monkeypatch.setattr(_generic, "DownloadBarManager", _FakeBarManager)


def _downloader():
    d = object.__new__(Generic_Downloader)
    d._pooled_keys = []
    d._direct_sources = [{"label": "video", "url": "http://example.test/video.mp4", "out_dir": ".", "source": {}}]

    md = SimpleNamespace(streams=[SimpleNamespace(type="audio", selected=True, is_external=False, drm=None)])
    md.set_key = lambda *a, **k: None
    md._prepare_labels = lambda: None
    md._get_prebuilt_tasks = lambda: []
    md._stop_check = lambda: False
    d._active = [(md, {})]

    d._graceful_stop = threading.Event()
    d._force_quit = threading.Event()
    d._interrupt_lock = threading.Lock()
    d._interrupt_count = 0
    d._interrupt_handled = 0
    d._track_done_events = {}
    d._track_results = {}
    d.download_id = None

    # Key pre-resolution hits the network/vault in the real implementation --
    # irrelevant to the concurrency behaviour under test.
    d._preresolve_direct_source_keys = lambda: None
    d._preresolve_manifest_keys = lambda: None

    return d, md


def test_direct_and_manifest_sources_download_concurrently():
    """Each fake download blocks until it observes the OTHER one has started, so this
    only completes if both run concurrently. With the old sequential
    (direct-then-manifest) behaviour, the direct download would still be running (and
    the manifest one not yet started) when the direct side checks -- timing out."""
    d, md = _downloader()

    direct_started = threading.Event()
    manifest_started = threading.Event()

    def fake_direct(entry, bm, relay):
        direct_started.set()
        assert manifest_started.wait(timeout=2), "manifest download never started concurrently with the direct one"
        entry["result"] = {"path": "video.mp4", "kind": "video", "role": "video", "language": "und", "size": 1}
        return True, False

    def fake_manifest_download(stream, bm):
        manifest_started.set()
        assert direct_started.wait(timeout=2), "direct download never started concurrently with the manifest one"

    d._download_one_direct_source = fake_direct
    md._download_stream = fake_manifest_download

    ok = d._download_mixed_sources()

    assert ok is True
    assert direct_started.is_set() and manifest_started.is_set()


def test_a_failed_direct_source_that_needs_stop_is_reported():
    d, md = _downloader()

    def fake_direct(entry, bm, relay):
        return False, True

    def fake_manifest_download(stream, bm):
        pass

    d._download_one_direct_source = fake_direct
    md._download_stream = fake_manifest_download

    assert d._download_mixed_sources() is False


def test_start_routes_mixed_sources_through_the_concurrent_download_path():
    """VibraVid/core/downloader/_generic.py's _start() must call
    _download_mixed_sources (not the old sequential _download_direct_sources +
    _run_downloads pair) whenever both direct and manifest sources are present."""
    import inspect

    source = inspect.getsource(Generic_Downloader._start)
    mixed_idx = source.index("_download_mixed_sources()")
    direct_idx = source.index("_download_direct_sources()")
    guard_idx = source.index("if self._direct_sources and self._active:")

    # The mixed-path call must be gated behind the "both present" guard, and must
    # appear before the old direct-only call in source order (the else branch).
    assert guard_idx < mixed_idx < direct_idx
