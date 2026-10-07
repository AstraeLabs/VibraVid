# 06.10.26

"""Tests for multi-manifest named-pipe live mux (NamedPipeMuxer + _manifest_streaming_mux_eligible)."""

import os
import sys
from types import SimpleNamespace

import pytest

from VibraVid.core.muxing.streaming_mux import NamedPipeMuxer


# ── unit: NamedPipeMuxer paths ────────────────────────────────────────────────

def test_pipe_paths_count():
    muxer = NamedPipeMuxer(n_pipes=3)
    assert len(muxer.pipe_paths) == 3


def test_pipe_paths_unique():
    m1 = NamedPipeMuxer(n_pipes=2)
    m2 = NamedPipeMuxer(n_pipes=2)
    # Two separate muxers must not share pipe names
    assert set(m1.pipe_paths).isdisjoint(set(m2.pipe_paths))


def test_pipe_paths_platform_format():
    muxer = NamedPipeMuxer(n_pipes=2)
    for p in muxer.pipe_paths:
        if sys.platform == "win32":
            assert p.startswith(r"\\.\pipe\vv_"), f"bad Windows pipe path: {p}"
        else:
            assert os.path.isabs(p), f"POSIX pipe path must be absolute: {p}"


# ── unit: _manifest_streaming_mux_eligible ───────────────────────────────────

def _make_stream(stype, selected=True, is_external=False):
    s = SimpleNamespace(type=stype, selected=selected, is_external=is_external)
    return s


def _make_generic_downloader(output_path, active_types, force_livemux=True):
    """Build a minimal Generic_Downloader-like object for eligibility tests."""
    import sys, os
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from VibraVid.core.downloader._generic import Generic_Downloader

    # Instantiate with dummy sources so __init__ runs
    gd = Generic_Downloader.__new__(Generic_Downloader)
    gd.output_path = output_path
    gd._active = []
    gd._direct_sources = []
    gd._dv_stream = None
    gd._pipe_mux_result = None
    gd._direct_streaming_mux_result = None

    for types_in_src in active_types:
        md = SimpleNamespace(
            streams=[_make_stream(t) for t in types_in_src]
        )
        gd._active.append((md, {}))

    return gd


def _patch_ffmpeg_found(monkeypatch):
    monkeypatch.setattr(
        "VibraVid.core.downloader._generic.get_ffmpeg_path",
        lambda: "ffmpeg",
        raising=True,
    )


def _patch_tracker(monkeypatch, force_livemux=True, no_livemux=False):
    from VibraVid.core.ui.tracker import context_tracker
    monkeypatch.setattr(context_tracker, "force_livemux", force_livemux, raising=False)
    monkeypatch.setattr(context_tracker, "no_livemux", no_livemux, raising=False)


def _patch_engine(monkeypatch, engine="ffmpeg"):
    monkeypatch.setattr(
        "VibraVid.core.downloader._generic.config_manager",
        SimpleNamespace(config=SimpleNamespace(get=lambda *a, **kw: engine)),
        raising=True,
    )


def test_eligible_basic(monkeypatch):
    """Standard HLS+DASH scenario: video manifest + audio manifest → eligible."""
    gd = _make_generic_downloader("out.mkv", [["video"], ["audio"]])
    _patch_tracker(monkeypatch)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is True


def test_eligible_requires_mkv(monkeypatch):
    gd = _make_generic_downloader("out.mp4", [["video"], ["audio"]])
    _patch_tracker(monkeypatch)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is False


def test_eligible_requires_two_active(monkeypatch):
    gd = _make_generic_downloader("out.mkv", [["video"]])
    _patch_tracker(monkeypatch)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is False


def test_eligible_allows_subtitle_with_separate_audio_manifest(monkeypatch):
    """HLS with video+subtitle AND separate DASH audio manifest: pipe mux is eligible.
    Subtitles go to disk; only video and audio are piped.
    """
    gd = _make_generic_downloader("out.mkv", [["video", "subtitle"], ["audio"]])
    _patch_tracker(monkeypatch)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is True


def test_eligible_rejects_manifest_with_mixed_video_audio(monkeypatch):
    """A manifest that carries BOTH video AND audio is handled by the single-manifest path."""
    gd = _make_generic_downloader("out.mkv", [["video", "audio"], ["audio"]])
    _patch_tracker(monkeypatch)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is False


def test_eligible_no_livemux_flag(monkeypatch):
    gd = _make_generic_downloader("out.mkv", [["video"], ["audio"]])
    _patch_tracker(monkeypatch, force_livemux=True, no_livemux=True)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is False


def test_eligible_without_force_livemux_when_possible(monkeypatch):
    gd = _make_generic_downloader("out.mkv", [["video"], ["audio"]])
    _patch_tracker(monkeypatch, force_livemux=False)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is True


def test_eligible_rejects_direct_sources(monkeypatch):
    gd = _make_generic_downloader("out.mkv", [["video"], ["audio"]])
    gd._direct_sources = [{"label": "direct0"}]  # non-empty → not eligible
    _patch_tracker(monkeypatch)
    _patch_ffmpeg_found(monkeypatch)
    _patch_engine(monkeypatch)
    assert gd._manifest_streaming_mux_eligible() is False


# ── unit: relay writer wired through _launch_streaming_mux_async ─────────────

def test_relay_writer_wired_to_merger(monkeypatch):
    """When _relay_mux_writer is set, _launch_streaming_mux_async must attach it directly."""
    from VibraVid.core.velora._decrypt_pipeline.streaming_mux import StreamingMuxMixin

    class _FakeMerger:
        def __init__(self):
            self.attached_fn = None

        def attach_feeder(self, fn):
            self.attached_fn = fn

    received = []

    def _my_writer(data: bytes) -> None:
        received.append(data)

    mixin = StreamingMuxMixin()
    mixin._relay_mux_writer = _my_writer

    merger = _FakeMerger()
    feeder_box = [None]

    t = mixin._launch_streaming_mux_async("mp4", object(), None, merger, feeder_box)
    t.join(timeout=5)

    assert merger.attached_fn is _my_writer, "attach_feeder not called with relay writer"
    assert feeder_box[0] is None, "feeder_box should stay None in relay mode (no per-manifest ffmpeg)"


def test_normal_path_unaffected_when_no_relay(monkeypatch):
    """Without _relay_mux_writer, _launch_streaming_mux_async must call _try_start_streaming_mux."""
    from VibraVid.core.velora._decrypt_pipeline.streaming_mux import StreamingMuxMixin

    calls = []

    class _Mixin(StreamingMuxMixin):
        _relay_mux_writer = None

        def _try_start_streaming_mux(self, *a, **kw):
            calls.append(True)
            return None  # simulate ineligible (no per-manifest ffmpeg started)

    class _FakeMerger:
        def attach_feeder(self, fn): pass

    mixin = _Mixin()
    feeder_box = [None]
    t = mixin._launch_streaming_mux_async("mp4", object(), None, _FakeMerger(), feeder_box)
    t.join(timeout=5)
    assert calls, "_try_start_streaming_mux was not called on the normal path"


# ── integration: CUSTOM.py URLs, max_segments=100 ────────────────────────────

_CUSTOM_VIDEO_URL = (
    "https://play-edge.itunes.apple.com/WebObjects/MZPlayLocal.woa/hls/playlist.m3u8"
    "?cc=AU&a=6793590242&id=1512725558&aec=UHD&l=en"
)
_CUSTOM_AUDIO_URL = (
    "https://a266vod-dash-pv-ta-amazon.akamaized.net/iad_2/60a1/c13e/1035/"
    "4670-9440-2e1cd81faff9/c494795c-8d68-4dad-845d-61924c9d1177_corrected.mpd"
)


def _urls_reachable() -> bool:
    """Quick HEAD/GET check on both URLs (timeout=5s each)."""
    try:
        from VibraVid.utils.http_client import create_client, get_headers
        for url in (_CUSTOM_VIDEO_URL, _CUSTOM_AUDIO_URL):
            with create_client(headers=get_headers(), timeout=5, follow_redirects=True) as c:
                r = c.get(url)
                if r.status_code >= 400:
                    return False
        return True
    except Exception:
        return False


_HAVE_LIVE_URLS = _urls_reachable()
skip_no_live = pytest.mark.skipif(not _HAVE_LIVE_URLS, reason="CUSTOM.py URLs not reachable (expired or offline)")


@skip_no_live
def test_pipe_mux_custom_100_segments(tmp_path):
    """Real download: 100 segments HLS video + DASH audio via named pipes → .mkv with video+audio."""
    import sys, os
    src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    from VibraVid.core.downloader._generic import Generic_Downloader
    from VibraVid.core.ui.tracker import context_tracker
    from VibraVid.core.muxing.helper.video.compat import get_stream_codecs

    context_tracker.force_livemux = True
    out = str(tmp_path / "custom_test.mkv")
    sources = [
        {"url": _CUSTOM_VIDEO_URL, "type": "video"},
        {"url": _CUSTOM_AUDIO_URL, "type": "audio"},
    ]

    gd = Generic_Downloader(sources=sources, output_path=out, max_segments=100)
    result_path, need_stop, error = gd.start()

    assert not need_stop, f"download was stopped: need_stop=True"
    assert error is None, f"download error: {error}"
    assert result_path and os.path.exists(result_path), f"output file not found: {result_path}"

    streams = get_stream_codecs(result_path)
    types = {s.get("codec_type") for s in streams}
    assert "video" in types, f"no video stream in output: {streams}"
    assert "audio" in types, f"no audio stream in output: {streams}"
    assert "subtitle" in types, f"no subtitle stream in output: {streams}"

    assert gd._pipe_mux_result is not None, "_pipe_mux_result not set — named-pipe mux did not run"
