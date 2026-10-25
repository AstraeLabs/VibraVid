# 25.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.mpd import DashParser

# 100s period of 30s segments -> 4 by duration, but the CDN only has 3 (endNumber), like the TIMvision subtitles.
_MPD = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT100S">
  <Period duration="PT100S">
    <AdaptationSet contentType="text" mimeType="application/mp4" codecs="stpp" lang="ita">
      <Role schemeIdUri="urn:mpeg:dash:role:2011" value="subtitle"/>
      <Representation id="r1" bandwidth="1000">
        <SegmentTemplate timescale="1000" media="$RepresentationID$/$Number$.mp4f" initialization="$RepresentationID$/0.mp4f" duration="30000" startNumber="1" {end}/>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""


def _media_numbers(end_attr: str) -> list[int]:
    parser = DashParser("https://cdn.example/path/manifest.mpd", content=_MPD.format(end=end_attr), quiet=True)
    parser.fetch_manifest()
    stream = parser.parse_streams()[0]
    return [s.number for s in stream.segments if s.seg_type == "media"]


def test_end_number_caps_the_segment_count():
    assert _media_numbers('endNumber="3"') == [1, 2, 3]


def test_without_end_number_the_period_duration_decides():
    assert _media_numbers("") == [1, 2, 3, 4]


def test_end_number_beyond_the_duration_does_not_add_segments():
    assert _media_numbers('endNumber="50"') == [1, 2, 3, 4]
