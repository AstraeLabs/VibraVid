# 05.10.26
# ruff: noqa: E402


import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.core.manifest.stream import Segment, Stream
from VibraVid.core.velora import _stream_vod
from VibraVid.core.velora._stream_vod import VodStreamMixin

KEY = "11" * 16 + ":" + "22" * 16
PLAYLIST = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:2
#EXT-X-MAP:URI="init.mp4"
#EXTINF:2.0,
seg0.m4s
#EXTINF:2.0,
seg1.m4s
#EXTINF:2.0,
seg2.m4s
#EXTINF:2.0,
seg3.m4s
#EXT-X-ENDLIST
"""


class FakeDrm:
    def __init__(self, kids=(), kid="N/A"):
        self._kids = list(kids)
        self.kid = kid

    def is_encrypted(self):
        return True

    def get_all_kids(self):
        return self._kids

    def get_kid_display(self):
        return self._kids[0] if self._kids else ""


class FakeDownloader(VodStreamMixin):
    def __init__(self, max_segments=None, key=None, refresh_fn=None):
        self.max_time = None
        self.max_segments = max_segments
        self.manifest_refresh_fn = refresh_fn
        self.key = key
        self.hls_enc_override = None
        self.generic_calls: list[dict] = []
        self.skipped: list[tuple] = []
        self.key_trials: list[tuple] = []

    def _build_headers(self):
        return {"X-H": "1"}

    def _assign_segment_durations(self, stream, dl_segs, headers):
        pass

    def _sync_estimated_size(self, stream, dl_segs, headers):
        pass

    def _skip_stream_no_key(self, stream, required, bar_manager=None, task_key=None):
        self.skipped.append((required,))

    def _spawn_live_key_trial(self, url, stream, seg_number, seg_url):
        self.key_trials.append((url, seg_number))

    def _download_stream_generic(self, dl_segs, stream, protocol, default_ext, bar_manager, live_decryption=False, seg_url_refresh_fn=None):
        self.generic_calls.append(
            dict(dl_segs=list(dl_segs), protocol=protocol, ext=default_ext, live=live_decryption, refresh=seg_url_refresh_fn)
        )


@pytest.fixture
def hls_net(monkeypatch):
    @contextmanager
    def _client(*a, **k):
        yield object()

    monkeypatch.setattr(_stream_vod, "create_client", _client)
    monkeypatch.setattr(_stream_vod, "get_with_retry", lambda c, url: SimpleNamespace(text=PLAYLIST))


def _numbers(call):
    return [(s["number"], s["seg_type"]) for s in call["dl_segs"]]


# ---- HLS ---------------------------------------------------------------------------------------------------------

def _hls_stream():
    return Stream(type="video", format="hls", playlist_url="https://cdn.test/v/index.m3u8")


def test_hls_plan_with_init(hls_net):
    dl = FakeDownloader()
    dl._download_hls_stream(_hls_stream(), None, live_decryption=True)
    (call,) = dl.generic_calls
    assert (call["protocol"], call["ext"], call["live"]) == ("hls", "ts", True)
    assert _numbers(call) == [(0, "init"), (1, "media"), (2, "media"), (3, "media"), (4, "media")]
    assert call["dl_segs"][0]["enc"] == {"method": "NONE"}
    assert call["dl_segs"][1]["url"].endswith("/v/seg0.m4s")


@pytest.mark.parametrize(
    "max_segments, expected_media",
    [(2, [1, 2]), ((1, 3), [2, 3]), ((2, None), [3, 4]), ((0, 1), [1])],
)
def test_hls_max_segments_keeps_the_init(hls_net, max_segments, expected_media):
    dl = FakeDownloader(max_segments=max_segments)
    dl._download_hls_stream(_hls_stream(), None)
    (call,) = dl.generic_calls
    nums = _numbers(call)
    assert nums[0] == (0, "init")
    assert [n for n, t in nums if t == "media"] == expected_media


def test_hls_refresh_closure(hls_net):
    dl = FakeDownloader(refresh_fn=lambda: "https://cdn.test/master.m3u8?hdnts=NEW")
    dl._download_hls_stream(_hls_stream(), None)
    refresh = dl.generic_calls[0]["refresh"]
    fresh = refresh([2, 4])
    assert sorted(fresh) == [2, 4]
    assert all(url.endswith("?hdnts=NEW") for url in fresh.values())
    assert fresh[2].split("?")[0].endswith("/v/seg1.m4s")


def test_hls_refresh_closure_without_or_with_empty_refresh_fn(hls_net):
    dl = FakeDownloader(refresh_fn=None)
    dl._download_hls_stream(_hls_stream(), None)
    assert dl.generic_calls[0]["refresh"]([1]) == {}
    dl = FakeDownloader(refresh_fn=lambda: None)
    dl._download_hls_stream(_hls_stream(), None)
    assert dl.generic_calls[0]["refresh"]([1]) == {}


def test_hls_skips_when_probed_kid_has_no_key(hls_net, monkeypatch):
    stream = _hls_stream()
    stream.drm = FakeDrm(kids=[])
    monkeypatch.setattr(_stream_vod, "_frag_init_probe", lambda segs, headers: (True, "ff" * 16))
    dl = FakeDownloader(key=KEY)
    dl._download_hls_stream(stream, None)
    assert dl.skipped == [("ff" * 16,)]
    assert dl.generic_calls == []


def test_hls_probed_kid_that_matches_proceeds(hls_net, monkeypatch):
    stream = _hls_stream()
    stream.drm = FakeDrm(kids=[])
    monkeypatch.setattr(_stream_vod, "_frag_init_probe", lambda segs, headers: (True, "11" * 16))
    dl = FakeDownloader(key=KEY)
    dl._download_hls_stream(stream, None)
    assert dl.skipped == []
    assert len(dl.generic_calls) == 1


def test_hls_without_playlist_url_or_segments_does_nothing(hls_net, monkeypatch):
    dl = FakeDownloader()
    dl._download_hls_stream(Stream(type="video", format="hls"), None)
    assert dl.generic_calls == []
    monkeypatch.setattr(_stream_vod, "get_with_retry", lambda c, url: SimpleNamespace(text="#EXTM3U\n#EXT-X-ENDLIST\n"))
    dl._download_hls_stream(_hls_stream(), None)
    assert dl.generic_calls == []


# ---- DASH --------------------------------------------------------------------------------------------------------

def _dash_stream(n_media=4, ranged=False, single_file=False):
    segs = [Segment(url="https://cdn.test/init.mp4", number=0, seg_type="init", byte_range="0-9" if ranged else "")]
    for i in range(n_media):
        if ranged:
            segs.append(Segment(url="https://cdn.test/file.mp4", number=i + 1, seg_type="media", byte_range=f"{10 + i * 100}-{109 + i * 100}"))
        elif single_file:
            segs.append(Segment(url="https://cdn.test/file.mp4", number=i + 1, seg_type="media"))
        else:
            segs.append(Segment(url=f"https://cdn.test/seg{i}.m4s", number=i + 1, seg_type="media"))
    return Stream(type="video", format="dash", segments=segs)


def test_dash_plan_plain():
    dl = FakeDownloader()
    dl._download_dash_stream(_dash_stream(), None, live_decryption=True)
    (call,) = dl.generic_calls
    assert (call["protocol"], call["ext"], call["live"]) == ("dash", "mp4", True)
    assert _numbers(call) == [(0, "init"), (1, "media"), (2, "media"), (3, "media"), (4, "media")]
    assert all(s["enc"] == {"method": "NONE"} for s in call["dl_segs"])


@pytest.mark.parametrize("max_segments, expected_media", [(2, [1, 2]), ((1, 3), [2, 3]), ((2, None), [3, 4])])
def test_dash_max_segments_keeps_the_init(max_segments, expected_media):
    dl = FakeDownloader(max_segments=max_segments)
    dl._download_dash_stream(_dash_stream(), None)
    nums = _numbers(dl.generic_calls[0])
    assert nums[0] == (0, "init")
    assert [n for n, t in nums if t == "media"] == expected_media


def test_dash_max_segments_without_a_leading_init_slices_everything():
    stream = _dash_stream()
    stream.segments = [s for s in stream.segments if s.seg_type == "media"]
    dl = FakeDownloader(max_segments=(1, 3))
    dl._download_dash_stream(stream, None)
    assert _numbers(dl.generic_calls[0]) == [(1, "media"), (2, "media")]


def test_dash_refresh_closure():
    dl = FakeDownloader(refresh_fn=lambda: "https://cdn.test/m.mpd?token=NEW")
    dl._download_dash_stream(_dash_stream(), None)
    fresh = dl.generic_calls[0]["refresh"]([1, 3])
    assert sorted(fresh) == [1, 3]
    assert all(url.endswith("?token=NEW") for url in fresh.values())
    dl = FakeDownloader(refresh_fn=None)
    dl._download_dash_stream(_dash_stream(), None)
    assert dl.generic_calls[0]["refresh"]([1]) == {}


def test_dash_skips_when_probed_kid_has_no_key(monkeypatch):
    stream = _dash_stream()
    stream.drm = FakeDrm(kids=[])
    monkeypatch.setattr(_stream_vod, "_frag_init_probe", lambda segs, headers: (False, "ee" * 16))
    dl = FakeDownloader(key=KEY)
    dl._download_dash_stream(stream, None)
    assert dl.skipped == [("ee" * 16,)]
    assert dl.generic_calls == []


@pytest.mark.parametrize("is_frag_init, expected_live", [(True, True), (False, False)])
def test_dash_byte_range_single_file_gates_live_decrypt(monkeypatch, is_frag_init, expected_live):
    monkeypatch.setattr(_stream_vod, "_frag_init_probe", lambda segs, headers: (is_frag_init, None))
    dl = FakeDownloader()
    dl._download_dash_stream(_dash_stream(ranged=True), None, live_decryption=True)
    assert dl.generic_calls[0]["live"] is expected_live


def test_dash_live_decrypt_flag_is_left_alone_when_off(monkeypatch):
    monkeypatch.setattr(_stream_vod, "_frag_init_probe", lambda segs, headers: (False, None))
    dl = FakeDownloader()
    dl._download_dash_stream(_dash_stream(ranged=True), None, live_decryption=False)
    assert dl.generic_calls[0]["live"] is False


def test_dash_whole_file_with_a_key_spawns_the_live_key_trial(monkeypatch):
    monkeypatch.setattr(_stream_vod, "build_dash_ranged_segments", lambda url, headers, chunk, timeout: ([], 0))
    stream = _dash_stream(n_media=1, single_file=True)
    stream.drm = FakeDrm(kids=["11" * 16])
    dl = FakeDownloader(key=KEY)
    dl._download_dash_stream(stream, None, live_decryption=True)
    assert dl.key_trials and dl.key_trials[0][0] == "https://cdn.test/file.mp4"


def test_dash_single_file_is_split_with_an_8mib_chunk(monkeypatch):
    seen = {}

    def fake_ranged(url, headers, chunk, timeout):
        seen["chunk"] = chunk
        return ([{"url": url, "headers": {"Range": "bytes=0-9"}, "enc": {"method": "NONE"}},
                 {"url": url, "headers": {"Range": "bytes=10-19"}, "enc": {"method": "NONE"}}], 20)

    monkeypatch.setattr(_stream_vod, "build_dash_ranged_segments", fake_ranged)
    dl = FakeDownloader()
    dl._download_dash_stream(_dash_stream(n_media=3, single_file=True), None)
    assert seen["chunk"] == 8 * 1024 * 1024
    assert _numbers(dl.generic_calls[0]) == [(0, "init"), (1, "media"), (2, "media")]


def test_dash_without_segments_does_nothing():
    dl = FakeDownloader()
    dl._download_dash_stream(Stream(type="video", format="dash"), None)
    assert dl.generic_calls == []


# ---- ISM ---------------------------------------------------------------------------------------------------------

def _ism_stream(n_media=4, ranged=False, single_file=False, encrypted=False):
    stream = _dash_stream(n_media=n_media, ranged=ranged, single_file=single_file)
    stream.format = "ism"
    if encrypted:
        stream.drm = FakeDrm(kids=["11" * 16], kid="11" * 16)
    return stream


def test_ism_plan_plain_and_encrypted_enc_dict():
    dl = FakeDownloader()
    dl._download_ism_stream(_ism_stream(), None, live_decryption=True)
    (call,) = dl.generic_calls
    assert (call["protocol"], call["ext"], call["live"]) == ("ism", "mp4", True)
    assert _numbers(call) == [(0, "init"), (1, "media"), (2, "media"), (3, "media"), (4, "media")]
    assert all(s["enc"] == {"method": "NONE"} for s in call["dl_segs"])

    dl = FakeDownloader(key=KEY)
    dl._download_ism_stream(_ism_stream(encrypted=True), None, live_decryption=True)
    assert all(s["enc"] == {"method": "playready-piff", "kid": "11" * 16} for s in dl.generic_calls[0]["dl_segs"])


def test_ism_encrypted_with_unknown_kid_has_no_kid_in_enc():
    stream = _ism_stream()
    stream.drm = FakeDrm(kids=["aa" * 16], kid="N/A")
    dl = FakeDownloader(key=KEY)
    dl._download_ism_stream(stream, None)
    assert dl.generic_calls[0]["dl_segs"][0]["enc"] == {"method": "playready-piff"}


@pytest.mark.parametrize("max_segments, expected", [(2, [(0, "init"), (1, "media")]), ((1, 3), [(1, "media"), (2, "media")])])
def test_ism_max_segments_slices_everything_including_the_init(max_segments, expected):
    """Unlike HLS/DASH, ISM does not protect the init segment when slicing (kept as-is)."""
    dl = FakeDownloader(max_segments=max_segments)
    dl._download_ism_stream(_ism_stream(), None)
    assert _numbers(dl.generic_calls[0]) == expected


def test_ism_refresh_closure():
    dl = FakeDownloader(refresh_fn=lambda: "https://cdn.test/m.ism/Manifest?sig=NEW")
    dl._download_ism_stream(_ism_stream(), None)
    fresh = dl.generic_calls[0]["refresh"]([1, 2])
    assert sorted(fresh) == [1, 2]
    assert all(url.endswith("?sig=NEW") for url in fresh.values())
    dl = FakeDownloader(refresh_fn=lambda: None)
    dl._download_ism_stream(_ism_stream(), None)
    assert dl.generic_calls[0]["refresh"]([1]) == {}


def test_ism_single_file_never_decrypts_live(monkeypatch):
    monkeypatch.setattr(_stream_vod, "build_dash_ranged_segments", lambda url, headers, chunk, timeout: ([], 0))
    dl = FakeDownloader()
    dl._download_ism_stream(_ism_stream(n_media=2, single_file=True), None, live_decryption=True)
    assert dl.generic_calls[0]["live"] is False


@pytest.mark.parametrize("is_frag_init, expected_live", [(True, True), (False, False)])
def test_ism_byte_range_single_file_gates_live_decrypt(monkeypatch, is_frag_init, expected_live):
    monkeypatch.setattr(_stream_vod, "_frag_init_probe", lambda segs, headers: (is_frag_init, None))
    dl = FakeDownloader()
    dl._download_ism_stream(_ism_stream(ranged=True), None, live_decryption=True)
    assert dl.generic_calls[0]["live"] is expected_live


def test_ism_skips_when_probed_kid_has_no_key(monkeypatch):
    stream = _ism_stream()
    stream.drm = FakeDrm(kids=[], kid="N/A")
    monkeypatch.setattr(_stream_vod, "_frag_init_probe", lambda segs, headers: (False, "ee" * 16))
    dl = FakeDownloader(key=KEY)
    dl._download_ism_stream(stream, None)
    assert dl.skipped == [("ee" * 16,)]
    assert dl.generic_calls == []


def test_ism_without_segments_does_nothing():
    dl = FakeDownloader()
    dl._download_ism_stream(Stream(type="video", format="ism"), None)
    assert dl.generic_calls == []
