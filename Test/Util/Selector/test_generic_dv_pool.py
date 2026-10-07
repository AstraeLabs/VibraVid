# 06.10.26
# ruff: noqa: E402

"""Custom multi-source download: with ``dv_auto`` off, a Dolby Vision rendition must not be auto-picked as a second video next to the explicit video role."""
import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from mock_streams import MockStream

from VibraVid.core.downloader._generic import Generic_Downloader


def _dv(stream_id, bitrate, height=1600):
    return MockStream(type="video", id=stream_id, height=height, width=height * 2, resolution=f"{height * 2}x{height}", codecs="dvh1.05.06", bitrate=bitrate)


def _downloader(video_filter="best", **filters):
    d = object.__new__(Generic_Downloader)
    d.custom_filters = {"video": video_filter, "audio": "best", "subtitle": "all", **filters}
    d._dv_stream = None
    d._no_match = False
    return d


def _apple_like_source(role="video"):
    """One HLS source where every rendition is Dolby Vision: the role picks the biggest, a smaller one is also 'best' in the pool."""
    streams = [_dv("low", 2_000_000, 720), _dv("main", 14_600_000), _dv("role_pick", 24_000_000)]
    return SimpleNamespace(streams=streams), {"role": role}


def _selected_ids(d, parsed):
    return sorted(s.id for s in d._select(parsed) if s.type == "video")


def test_dv_auto_off_keeps_only_the_explicit_video_role():
    d = _downloader(dv_auto=False)

    assert _selected_ids(d, [_apple_like_source()]) == ["role_pick"]


def test_dv_auto_off_also_with_an_audio_only_second_source():
    d = _downloader(dv_auto=False)
    audio = SimpleNamespace(streams=[MockStream(type="audio", id="a", language="en", resolved_language="en-US", codecs="ec-3", bitrate=192_000)])

    selected = d._select([_apple_like_source(), (audio, {"role": "audio"})])

    assert sorted(s.id for s in selected if s.type == "video") == ["role_pick"]
    assert [s.id for s in selected if s.type == "audio"] == ["a"]


def test_dv_auto_on_still_lets_a_dolby_vision_stream_into_the_pool():
    d = _downloader(dv_auto=True)

    assert "main" in _selected_ids(d, [_apple_like_source()])


def test_hybrid_still_lets_a_dolby_vision_stream_into_the_pool():
    d = _downloader(video_filter="hybrid", dv_auto=False)

    assert "main" in _selected_ids(d, [_apple_like_source()])


@pytest.mark.parametrize("dv_auto, expected", [(False, False), (True, True)])
def test_companion_possible_follows_dv_auto(dv_auto, expected):
    assert _downloader(dv_auto=dv_auto)._dv_companion_possible("best") is expected


def test_companion_possible_for_hybrid_even_when_dv_auto_is_off():
    assert _downloader(dv_auto=False)._dv_companion_possible(" Hybrid ") is True
