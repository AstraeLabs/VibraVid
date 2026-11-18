# 01.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import Segment, Stream

# On-demand profile: one file per Representation, every SegmentURL carries its own exact byte range.
# Bandwidth is the peak (8 Mbps over 20s = 20 MB) but the ranges add up to 3_000_000 bytes.
_MPD_SEGMENT_LIST = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT20S"
     profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
  <Period duration="PT20S">
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="video=8000000" bandwidth="8000000" codecs="avc1.640028" width="1920" height="1080">
        <BaseURL>video.mp4</BaseURL>
        <SegmentList duration="10" timescale="1">
          <Initialization range="0-99"/>
          <SegmentURL mediaRange="1000-1999"/>
          <SegmentURL mediaRange="2000-2999999"/>
        </SegmentList>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def _parse(content: str) -> list[Stream]:
    parser = DashParser("https://cdn.example/path/manifest.mpd", content=content, quiet=True)
    parser.fetch_manifest()
    return parser.parse_streams()


def test_range_size_is_inclusive_byte_length():
    assert Segment("u", 1, byte_range="0-99").range_size() == 100
    assert Segment("u", 1, byte_range="2000-2999999").range_size() == 2_998_000


def test_range_size_is_zero_when_absent_or_open_ended():
    assert Segment("u", 1).range_size() == 0
    assert Segment("u", 1, byte_range="100-").range_size() == 0
    assert Segment("u", 1, byte_range="abc-def").range_size() == 0
    assert Segment("u", 1, byte_range="50-10").range_size() == 0


def test_segment_list_uses_media_range_not_nominal_bandwidth():
    stream = _parse(_MPD_SEGMENT_LIST)[0]
    nominal = int(stream.bitrate / 8 * stream.duration)

    estimate = stream.compute_estimated_size()

    assert stream.estimated_size_exact is True
    # init is widened to the first media range (0-999), so the total is the end of the last range
    assert estimate == 2_999_999 + 1
    assert estimate < nominal


def test_segment_list_every_segment_has_its_size():
    stream = _parse(_MPD_SEGMENT_LIST)[0]
    assert [s.size for s in stream.segments] == [1000, 1000, 2_998_000]


def test_bitrate_fallback_is_flagged_not_exact():
    stream = Stream(type="video", bitrate=8_000_000, duration=20.0)
    stream.add_segment(Segment("u", 1))

    assert stream.compute_estimated_size() == 20_000_000
    assert stream.estimated_size_exact is False


def test_partially_sized_media_segments_are_not_exact():
    stream = Stream(type="video", bitrate=1_000_000, duration=10.0)
    stream.add_segment(Segment("u", 1, size=500))
    stream.add_segment(Segment("u", 2))

    stream.compute_estimated_size()

    assert stream.estimated_size_exact is False


def test_exact_flag_is_reset_when_sizes_disappear():
    stream = Stream(type="video", bitrate=8_000_000, duration=20.0)
    stream.add_segment(Segment("u", 1, size=123))
    stream.compute_estimated_size()
    assert stream.estimated_size_exact is True

    stream.segments[0].size = 0
    stream.compute_estimated_size()
    assert stream.estimated_size_exact is False
