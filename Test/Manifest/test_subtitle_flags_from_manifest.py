# 21.11.26
# ruff: noqa: E402

"""Every manifest protocol must name the same subtitle variants the same way (forced / cc / sdh / hi, lossless)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.custom import CustomParser
from VibraVid.core.manifest.ism import ISMParser
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.utils.selector import StreamSelector

# What each fixture below describes, whatever the protocol
EXPECTED = {"it", "it_forced", "en", "en_cc", "en_sdh", "en_hi"}

HLS_MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Italiano",LANGUAGE="it",AUTOSELECT=YES,DEFAULT=YES,URI="s/it.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="Italiano forzati",LANGUAGE="it",FORCED=YES,URI="s/it_forced.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",LANGUAGE="en",AUTOSELECT=YES,URI="s/en.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English SDH",LANGUAGE="en",CHARACTERISTICS="public.accessibility.transcribes-spoken-dialog,public.accessibility.describes-music-and-sound",URI="s/en_sdh.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English CC",LANGUAGE="en",URI="s/en_cc.m3u8"
#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English (HI)",LANGUAGE="en",URI="s/en_hi.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1280x720,SUBTITLES="subs"
video/720p.m3u8
"""

DASH_MPD = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT10S" minBufferTime="PT2S">
 <Period>
  <AdaptationSet id="1" contentType="video" mimeType="video/mp4">
   <Representation id="v" bandwidth="2000000" width="1280" height="720" codecs="avc1.640028">
    <BaseURL>v.mp4</BaseURL><SegmentBase indexRange="0-100"><Initialization range="0-50"/></SegmentBase>
   </Representation>
  </AdaptationSet>
  <AdaptationSet id="2" contentType="text" mimeType="text/vtt" lang="it">
   <Representation id="it" bandwidth="100"><BaseURL>it.vtt</BaseURL></Representation>
  </AdaptationSet>
  <AdaptationSet id="3" contentType="text" mimeType="text/vtt" lang="it">
   <Role schemeIdUri="urn:mpeg:dash:role:2011" value="forced-subtitle"/>
   <Representation id="it_forced" bandwidth="100"><BaseURL>it_f.vtt</BaseURL></Representation>
  </AdaptationSet>
  <AdaptationSet id="4" contentType="text" mimeType="text/vtt" lang="en">
   <Representation id="en" bandwidth="100"><BaseURL>en.vtt</BaseURL></Representation>
  </AdaptationSet>
  <AdaptationSet id="5" contentType="text" mimeType="text/vtt" lang="en">
   <Role schemeIdUri="urn:mpeg:dash:role:2011" value="caption"/>
   <Representation id="en_cc" bandwidth="100"><BaseURL>en_cc.vtt</BaseURL></Representation>
  </AdaptationSet>
  <AdaptationSet id="6" contentType="text" mimeType="text/vtt" lang="en">
   <Accessibility schemeIdUri="urn:tva:metadata:cs:AudioPurposeCS:2007" value="2"/>
   <Representation id="en_hi" bandwidth="100"><BaseURL>en_hi.vtt</BaseURL></Representation>
  </AdaptationSet>
  <AdaptationSet id="7" contentType="text" mimeType="text/vtt" lang="en">
   <Label>English SDH</Label>
   <Representation id="en_sdh" bandwidth="100"><BaseURL>en_sdh.vtt</BaseURL></Representation>
  </AdaptationSet>
 </Period>
</MPD>
"""


def _ism_index(subtype: str, lang: str, name: str = "") -> str:
    name_attr = f' Name="{name}"' if name else ""
    return (
        f'<StreamIndex Type="text" Subtype="{subtype}" Language="{lang}"{name_attr} Chunks="1" '
        f'Url="QualityLevels({{bitrate}})/Fragments(textstream={{start time}})">'
        '<QualityLevel Index="0" Bitrate="1000" FourCC="TTML"/><c d="100000000"/></StreamIndex>'
    )


ISM_XML = (
    '<?xml version="1.0"?><SmoothStreamingMedia MajorVersion="2" MinorVersion="0" TimeScale="10000000" Duration="100000000">'
    + _ism_index("SUBT", "it")
    + _ism_index("FORC", "it")
    + _ism_index("SUBT", "en")
    + _ism_index("CAPT", "en")
    + _ism_index("SUBT", "en", name="English SDH")
    + _ism_index("SUBT", "en", name="English HI")
    + "</SmoothStreamingMedia>"
)

CUSTOM_JSON = json.dumps(
    {
        "vibravid_manifest": True,
        "tracks": [
            {"type": "subtitle", "id": "t-it", "language": "it", "segments": {"list": [{"url": "https://h/it.vtt"}]}},
            {"type": "subtitle", "id": "t-itf", "language": "it", "forced": True, "segments": {"list": [{"url": "https://h/itf.vtt"}]}},
            {"type": "subtitle", "id": "t-en", "language": "en", "segments": {"list": [{"url": "https://h/en.vtt"}]}},
            {"type": "subtitle", "id": "t-cc", "language": "en", "is_cc": True, "segments": {"list": [{"url": "https://h/cc.vtt"}]}},
            {"type": "subtitle", "id": "t-sdh", "language": "en", "is_sdh": True, "segments": {"list": [{"url": "https://h/sdh.vtt"}]}},
            {"type": "subtitle", "id": "t-hi", "language": "en", "is_hi": True, "segments": {"list": [{"url": "https://h/hi.vtt"}]}},
        ],
    }
)


def _hls():
    parser = HLSParser(m3u8_url="file:///dummy/master.m3u8", content=HLS_MASTER)
    parser.fetch_manifest()
    with patch.object(HLSParser, "_resolve_drm", lambda *_args: None):
        return [s for s in parser.parse_streams() if s.type == "subtitle"]


def _dash():
    parser = DashParser("https://cdn.test/manifest.mpd", content=DASH_MPD)
    assert parser.fetch_manifest()
    return [s for s in parser.parse_streams() if s.type == "subtitle"]


def _ism():
    parser = ISMParser("https://cdn.test/video.ism/manifest", content=ISM_XML)
    assert parser.fetch_manifest()
    return [s for s in parser.parse_streams() if s.type == "subtitle"]


def _custom():
    parser = CustomParser("https://cdn.test/m.json", content=CUSTOM_JSON)
    assert parser.fetch_manifest()
    return [s for s in parser.parse_streams() if s.type == "subtitle"]


PARSERS = {"hls": _hls, "dash": _dash, "ism": _ism, "custom": _custom}


def _names(streams):
    return {s.canonical_name for s in streams}


def _pick(factory, value):
    streams = factory()
    StreamSelector("false", "false", value).apply(streams)
    return {s.canonical_name for s in streams if s.selected}


def test_every_protocol_names_the_same_variants_the_same_way():
    for proto, factory in PARSERS.items():
        streams = factory()
        assert _names(streams) == EXPECTED, f"{proto}: {sorted(_names(streams))}"
        assert len({s.id for s in streams}) == len(streams), f"{proto}: ids collide: {[s.id for s in streams]}"


def test_flags_are_never_folded_into_each_other():
    for proto, factory in PARSERS.items():
        by_name = {s.canonical_name: s for s in factory()}
        assert by_name["it_forced"].sub_flags.names() == ("forced",), proto
        assert by_name["en_cc"].sub_flags.names() == ("cc",), proto
        assert by_name["en_sdh"].sub_flags.names() == ("sdh",), proto
        assert by_name["en_hi"].sub_flags.names() == ("hi",), proto
        assert by_name["en"].sub_flags.names() == () and by_name["it"].sub_flags.names() == (), proto


def test_selecting_each_variant_works_on_every_protocol():
    for proto, factory in PARSERS.items():
        assert _pick(factory, "ita_forced") == {"it_forced"}, proto
        assert _pick(factory, "eng_cc") == {"en_cc"}, proto
        assert _pick(factory, "eng_forced") == set(), proto


def test_sdh_and_hi_requests_prefer_their_own_spelling():
    for proto, factory in PARSERS.items():
        assert _pick(factory, "eng_sdh") == {"en_sdh"}, proto
        assert _pick(factory, "eng_hi") == {"en_hi"}, proto


def test_sdh_request_falls_back_to_hi_track_and_never_to_cc():
    for proto, factory in PARSERS.items():
        streams = [s for s in factory() if s.canonical_name != "en_sdh"]
        StreamSelector("false", "false", "eng_sdh").apply(streams)
        assert {s.canonical_name for s in streams if s.selected} == {"en_hi"}, proto


def test_plain_language_request_keeps_every_variant():
    for proto, factory in PARSERS.items():
        assert _pick(factory, "eng") == {"en", "en_cc", "en_sdh", "en_hi"}, proto


def test_hls_sdh_name_does_not_imply_cc_and_caption_role_does_not_imply_sdh():
    by_name = {s.canonical_name: s for s in _hls()}
    assert not by_name["en_sdh"].is_cc
    by_name = {s.canonical_name: s for s in _dash()}
    assert by_name["en_cc"].is_cc and not by_name["en_cc"].is_sdh and not by_name["en_cc"].is_hi


def test_hls_forced_name_is_not_annotated_twice():
    names = {s.id: s.name for s in _hls()}
    # "Italiano forzati" does not say "forced": it gets the [Forced] marker; a name that already says it is left alone
    assert names["subs:it_forced"] == "Italiano forzati [Forced]"
    master = HLS_MASTER.replace('NAME="Italiano forzati"', 'NAME="Italiano (Forced)"')
    parser = HLSParser(m3u8_url="file:///dummy/master.m3u8", content=master)
    parser.fetch_manifest()
    with patch.object(HLSParser, "_resolve_drm", lambda *_args: None):
        forced = next(s for s in parser.parse_streams() if s.type == "subtitle" and s.forced)
    assert forced.name == "Italiano (Forced)"
