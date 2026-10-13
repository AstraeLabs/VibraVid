# 13.10.26
# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.stream import Segment, Stream
from VibraVid.core.velora.base import BaseMediaDownloader
from VibraVid.core.velora.util._hls import parse_hls_variant_playlist


def _seg(period_idx: int, n: int = 1) -> list[Segment]:
    return [Segment(url=f"https://cdn.example/p{period_idx}-s{i}.m4s", number=i, period_idx=period_idx) for i in range(n)]


def _streams() -> list[Stream]:
    video = Stream(type="video", id="v1", codecs="avc1.64001f", width=960, height=540)
    video.segments = _seg(0, 2) + _seg(1, 1) + _seg(2, 2)
    audio_it = Stream(type="audio", id="a-it", language="it", codecs="mp4a.40.2")
    audio_it.segments = _seg(0, 2) + _seg(2, 2)
    audio_en = Stream(type="audio", id="a-en", language="en", codecs="mp4a.40.2")
    audio_en.segments = _seg(1, 1)
    return [video, audio_it, audio_en]


def _ns(fn, streams):
    return SimpleNamespace(ad_decision_fn=fn, streams=streams)


def test_dash_enforcement_is_symmetric_across_tracks():
    ns = _ns(lambda kind, content, ctx: {"drop_period_idx": [1], "reason": "t"}, _streams())
    kept = BaseMediaDownloader._apply_ad_decision(ns, "dash", SimpleNamespace(raw_content="<MPD/>"))
    by_id = {s.id: sorted({sg.period_idx for sg in s.segments}) for s in kept}
    assert by_id["v1"] == [0, 2]
    assert by_id["a-it"] == [0, 2]
    # en was ad-only -> removed entirely, so the mux never trims to ~5s
    assert "a-en" not in by_id


def test_dash_noop_without_callback_or_decision():
    streams = _streams()
    ns = _ns(None, streams)
    assert BaseMediaDownloader._apply_ad_decision(ns, "dash", SimpleNamespace(raw_content="")) is streams
    ns = _ns(lambda k, c, x: {}, streams)
    assert BaseMediaDownloader._apply_ad_decision(ns, "dash", SimpleNamespace(raw_content="")) is streams
    # non-DASH manifests are untouched even with a callback
    ns = _ns(lambda k, c, x: {"drop_period_idx": [0]}, streams)
    assert BaseMediaDownloader._apply_ad_decision(ns, "hls", SimpleNamespace(raw_content="")) is streams


def test_dash_decision_dropping_all_video_keeps_everything():
    streams = _streams()
    ns = _ns(lambda k, c, x: {"drop_period_idx": [0, 1, 2]}, streams)
    assert BaseMediaDownloader._apply_ad_decision(ns, "dash", SimpleNamespace(raw_content="")) is streams
    assert len(streams[0].segments) == 5  # untouched (no partial mutation)


def _it_track(track_id: str, periods: list[int], lang="it", codecs="mp4a.40.2", bw=102000):
    s = Stream(type="audio", id=track_id, language=lang, codecs=codecs)
    s.channels = "2"
    s.sample_rate = 48000
    for p in periods:
        s.segments.append(Segment(url=f"https://cdn.example/{track_id}-p{p}.m4s", number=p, period_idx=p))
    return s


def test_dash_rejoins_split_audio_continuations():
    v = Stream(type="video", id="v1", codecs="avc1.64001f", width=960, height=540)
    v.segments = _seg(0, 1) + _seg(2, 1)
    streams = [v, _it_track("a-x", [0]), _it_track("a-y", [2])]
    ns = _ns(lambda k, c, x: {"drop_period_idx": [1], "reason": "t"}, streams)
    kept = BaseMediaDownloader._apply_ad_decision(ns, "dash", SimpleNamespace(raw_content=""))
    it_tracks = [s for s in kept if s.type == "audio"]
    assert len(it_tracks) == 1
    assert sorted(sg.period_idx for sg in it_tracks[0].segments) == [0, 2]


def test_dash_overlapping_same_lang_tracks_are_not_merged():
    v = Stream(type="video", id="v1", codecs="avc1.64001f", width=960, height=540)
    v.segments = _seg(0, 1) + _seg(2, 1)
    streams = [v, _it_track("a-stereo", [0, 2]), _it_track("a-commentary", [0, 2])]
    ns = _ns(lambda k, c, x: {"drop_period_idx": [1], "reason": "t"}, streams)
    kept = BaseMediaDownloader._apply_ad_decision(ns, "dash", SimpleNamespace(raw_content=""))
    assert len([s for s in kept if s.type == "audio"]) == 2


_HLS_TWO_MAPS = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MAP:URI="https://cdn.example/init-main.mp4"
#EXTINF:6.0,
seg-0.ts
#EXTINF:6.0,
seg-1.ts
#EXT-X-MAP:URI="https://cdn.example/init-ads.mp4"
#EXTINF:6.0,
ad-0.ts
#EXT-X-ENDLIST
"""


def test_hls_block_filter_applies_service_decision():
    segs, init = parse_hls_variant_playlist(
        _HLS_TWO_MAPS, "https://cdn.example/",
        ad_block_filter=lambda blocks, content: {"https://cdn.example/init-main.mp4"},
    )
    assert [s["url"] for s in segs] == ["https://cdn.example/seg-0.ts", "https://cdn.example/seg-1.ts"]
    assert init == "https://cdn.example/init-main.mp4"


def test_hls_without_filter_keeps_dominant_block():
    segs, _ = parse_hls_variant_playlist(_HLS_TWO_MAPS, "https://cdn.example/")
    assert len(segs) == 2  # legacy behavior unchanged


_HLS_RANGED = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:6.0,
https://cdn.example/content/a-00000.ts
#EXTINF:6.0,
https://cdn.example/content/a-00001.ts
#EXT-X-DISCONTINUITY
#EXTINF:6.0,
https://cdn.example/ads/ad_bumper-00000.ts
#EXT-X-DISCONTINUITY
#EXTINF:6.0,
https://cdn.example/content/a-00002.ts
#EXT-X-ENDLIST
"""


def test_hls_seg_idx_marks_playlist_order():
    segs, _ = parse_hls_variant_playlist(_HLS_RANGED, "https://cdn.example/")
    assert [s["playlist_idx"] for s in segs] == [0, 1, 2, 3]


def test_hls_seg_filter_drops_service_decision():
    from VibraVid.services.plutotv.ads import pluto_ad_decision

    def _seg_filter(segs, content):
        decision = pluto_ad_decision("hls", content, None) or {}
        drop = set(decision.get("drop_seg_idx") or [])
        if not drop:
            return None
        return {s["playlist_idx"] for s in segs if s["playlist_idx"] not in drop}

    # emulate the core enforcement: block filter (none here) then seg filter
    segs, _ = parse_hls_variant_playlist(_HLS_RANGED, "https://cdn.example/")
    keep = _seg_filter(segs, _HLS_RANGED)
    kept = [s for s in segs if s["playlist_idx"] in keep]
    assert [s["url"] for s in kept] == [
        "https://cdn.example/content/a-00000.ts",
        "https://cdn.example/content/a-00001.ts",
        "https://cdn.example/content/a-00002.ts",
    ]


def _ism_stream(kind, sid, urls):
    s = Stream(type=kind, id=sid, language="it" if kind == "audio" else "und", codecs="mp4a.40.2")
    for i, u in enumerate(urls):
        s.segments.append(Segment(url=u, number=i))
    return s


_ISM_URLS_VIDEO = [f"https://cdn.example/video/seg-{i}.m4s" for i in range(4)]
_ISM_URLS_AD = [f"https://cdn.example/ads/preroll-{i}.m4s" for i in range(2)]


def test_ism_enforcement_drops_url_substrings_symmetrically():
    v = _ism_stream("video", "v1", _ISM_URLS_VIDEO)
    a = _ism_stream("audio", "a1", _ISM_URLS_VIDEO)
    ad = _ism_stream("audio", "a-ad", _ISM_URLS_AD)
    ns = _ns(lambda k, c, x: {"drop_url_substrings": ["preroll"]}, [v, a, ad])
    kept = BaseMediaDownloader._apply_ad_decision(ns, "ism", SimpleNamespace(raw_content="<Smooth/>"))
    assert [s.id for s in kept] == ["v1", "a1"]
    assert all(len(s.segments) == 4 for s in kept)


def test_ism_noop_and_video_safety():
    streams = [_ism_stream("video", "v1", _ISM_URLS_VIDEO)]
    ns = _ns(None, streams)
    assert BaseMediaDownloader._apply_ad_decision(ns, "ism", SimpleNamespace(raw_content="")) is streams
    ns = _ns(lambda k, c, x: {"drop_url_substrings": ["video"]}, streams)
    assert BaseMediaDownloader._apply_ad_decision(ns, "ism", SimpleNamespace(raw_content="")) is streams
    # other protocols untouched by ism/dash-only callbacks
    ns = _ns(lambda k, c, x: {"drop_url_substrings": ["x"]}, streams)
    assert BaseMediaDownloader._apply_ad_decision(ns, "hls", SimpleNamespace(raw_content="")) is streams
