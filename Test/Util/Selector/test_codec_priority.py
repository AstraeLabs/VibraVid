# 23.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest
from mock_streams import MockStream

from VibraVid.core.utils.selector import StreamSelector


def _pool():
    return [
        MockStream(type="video", height=1080, codecs="avc1.640028", bitrate=7_000_000, id="avc1080"),
        MockStream(type="video", height=1080, codecs="hvc1.2.4.L120.B0", bitrate=4_000_000, id="hevc1080"),
        MockStream(type="video", height=720, codecs="hvc1.2.4.L93.B0", bitrate=2_000_000, id="hevc720"),
    ]


def _selected(streams):
    return [s for s in streams if s.type == "video" and s.selected][0].id


def test_without_a_priority_the_highest_bitrate_wins_at_equal_height():
    streams = _pool()
    StreamSelector("best", "false", "false").apply(streams)
    assert _selected(streams) == "avc1080"


@pytest.mark.parametrize("priority, expected", [
    (["hevc", "avc"], "hevc1080"),
    (["H265", "H264"], "hevc1080"),
    (["avc", "hevc"], "avc1080"),
    (["AVC"], "avc1080"),
])
def test_the_requested_codec_order_decides_at_equal_height(priority, expected):
    streams = _pool()
    StreamSelector("best", "false", "false", codec_priority=priority).apply(streams)
    assert _selected(streams) == expected


def test_a_taller_stream_still_beats_a_preferred_codec():
    streams = _pool()
    streams[0].height = 2160
    StreamSelector("best", "false", "false", codec_priority=["hevc", "avc"]).apply(streams)
    assert _selected(streams) == "avc1080"


def test_a_codec_missing_from_the_priority_ranks_last():
    streams = _pool()
    StreamSelector("best", "false", "false", codec_priority=["hevc"]).apply(streams)
    assert _selected(streams) == "hevc1080"
