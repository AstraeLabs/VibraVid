# 21.11.26
# ruff: noqa: E402

"""Audio description (ad) is detected the same way by every protocol, never confused with a subtitle flag, and never replaces the main audio."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))
sys.path.insert(0, str(workspace_root / "Test" / "Util" / "Selector"))

from mock_streams import MockStream

from VibraVid.core.manifest.custom import CustomParser
from VibraVid.core.manifest.ism import ISMParser
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.utils.language import language_variants, resolve_ietf, track_title_and_iso_lang
from VibraVid.core.utils.selector import StreamSelector
from VibraVid.core.utils.track_order import order_tracks

HLS_MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",LANGUAGE="en",DEFAULT=YES,URI="a/en.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English Audio Description",LANGUAGE="en",CHARACTERISTICS="public.accessibility.describes-video",URI="a/en_ad.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Catala",LANGUAGE="ca-AD",URI="a/ca.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1280x720,AUDIO="aud"
video/720p.m3u8
"""


def _dash_audio(rep_id, extra=""):
    return (
        f'<AdaptationSet contentType="audio" mimeType="audio/mp4" lang="en">{extra}'
        f'<Representation id="{rep_id}" bandwidth="128000" codecs="mp4a.40.2" audioSamplingRate="48000">'
        f'<BaseURL>{rep_id}.mp4</BaseURL><SegmentBase indexRange="0-100"><Initialization range="0-50"/></SegmentBase>'
        "</Representation></AdaptationSet>"
    )


DASH_MPD = (
    '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT10S" minBufferTime="PT2S"><Period>'
    + _dash_audio("main")
    + _dash_audio("ad_role", '<Role schemeIdUri="urn:mpeg:dash:role:2011" value="description"/>')
    + _dash_audio("ad_purpose", '<Accessibility schemeIdUri="urn:tva:metadata:cs:AudioPurposeCS:2007" value="1"/>')
    + "</Period></MPD>"
)


def _ism_audio(name=""):
    name_attr = f' Name="{name}"' if name else ""
    return (
        f'<StreamIndex Type="audio" Language="en"{name_attr} Chunks="1" Url="QualityLevels({{bitrate}})/Fragments(audio={{start time}})">'
        '<QualityLevel Index="0" Bitrate="128000" FourCC="AACL" SamplingRate="48000" Channels="2"/><c d="100000000"/></StreamIndex>'
    )


ISM_XML = (
    '<?xml version="1.0"?><SmoothStreamingMedia MajorVersion="2" MinorVersion="0" TimeScale="10000000" Duration="100000000">'
    + _ism_audio()
    + _ism_audio("English Audio Description")
    + "</SmoothStreamingMedia>"
)

CUSTOM_JSON = json.dumps(
    {
        "vibravid_manifest": True,
        "tracks": [
            {"type": "audio", "id": "a-main", "language": "en", "segments": {"list": [{"url": "https://h/a.m4a"}]}},
            {"type": "audio", "id": "a-ad", "language": "en", "is_ad": True, "segments": {"list": [{"url": "https://h/ad.m4a"}]}},
        ],
    }
)


def _hls():
    parser = HLSParser(m3u8_url="file:///dummy/master.m3u8", content=HLS_MASTER)
    parser.fetch_manifest()
    with patch.object(HLSParser, "_resolve_drm", lambda *_args: None):
        return [s for s in parser.parse_streams() if s.type == "audio"]


def _dash():
    parser = DashParser("https://cdn.test/manifest.mpd", content=DASH_MPD)
    assert parser.fetch_manifest()
    return [s for s in parser.parse_streams() if s.type == "audio"]


def _ism():
    parser = ISMParser("https://cdn.test/video.ism/manifest", content=ISM_XML)
    assert parser.fetch_manifest()
    return [s for s in parser.parse_streams() if s.type == "audio"]


def _custom():
    parser = CustomParser("https://cdn.test/m.json", content=CUSTOM_JSON)
    assert parser.fetch_manifest()
    return [s for s in parser.parse_streams() if s.type == "audio"]


PARSERS = {"hls": _hls, "dash": _dash, "ism": _ism, "custom": _custom}


def test_each_protocol_flags_the_audio_description_and_only_it():
    expected_ad = {"hls": {"English Audio Description"}, "dash": {"ad_role", "ad_purpose"}, "ism": {"English Audio Description"}, "custom": {"a-ad"}}
    for proto, factory in PARSERS.items():
        streams = factory()
        ads = {(s.name if proto in ("hls", "ism") else s.id) for s in streams if s.is_ad}
        assert ads == expected_ad[proto], f"{proto}: {ads}"
        # an audio description says nothing about subtitles
        assert not any(s.is_sdh or s.is_cc or s.is_hi or s.forced for s in streams), proto


def test_audio_description_never_shares_an_id_with_the_main_track():
    for proto, factory in PARSERS.items():
        streams = factory()
        assert len({s.id for s in streams}) == len(streams), f"{proto}: {[s.id for s in streams]}"


def test_andorra_region_is_not_an_audio_description():
    ca = next(s for s in _hls() if s.language == "ca-AD")
    assert not ca.is_ad


def _audio(id_, bitrate, ad=False):
    return MockStream(type="audio", language="eng", resolved_language="en-US", bitrate=bitrate, codecs="mp4a", id=id_, is_ad=ad)


def _picked(streams, value):
    StreamSelector("false", value, "false").apply(streams)
    return {s.id for s in streams if s.type == "audio" and s.selected}


def test_main_audio_wins_even_if_the_description_has_a_higher_bitrate():
    assert _picked([_audio("main", 128000), _audio("ad", 256000, ad=True)], "eng") == {"main"}
    assert _picked([_audio("main", 128000), _audio("ad", 64000, ad=True)], "eng") == {"main"}


def test_audio_description_is_requested_with_the_ad_suffix():
    assert _picked([_audio("main", 128000), _audio("ad", 64000, ad=True)], "eng_ad") == {"ad"}
    # like any audio request that matches nothing: the best available track, or nothing in strict mode
    assert _picked([_audio("main", 128000)], "eng_ad") == {"main"}
    streams = [_audio("main", 128000)]
    selector = StreamSelector("false", "eng_ad", "false", strict_no_match=True)
    selector.apply(streams)
    assert not streams[0].selected and selector.no_match


def test_audio_description_is_used_when_it_is_all_there_is():
    assert _picked([_audio("ad", 64000, ad=True)], "eng") == {"ad"}


def test_order_puts_the_description_after_the_main_audio_of_its_language():
    tracks = [{"language": "en-us", "ad": True, "id": "ad"}, {"language": "en-us", "id": "main"}, {"language": "it-it", "id": "it"}]
    assert [t["id"] for t in order_tracks(tracks, "audio")] == ["main", "it", "ad"] or [t["id"] for t in order_tracks(tracks, "audio")][0] == "main"
    assert [t["id"] for t in order_tracks(tracks, "audio", "ita|eng")] == ["it", "main", "ad"]


def test_ad_suffix_never_becomes_the_andorra_region():
    assert resolve_ietf("ita_ad") == "ita"
    assert resolve_ietf("it-IT_ad") == "it-IT"
    assert resolve_ietf("ca-ad") == "ca-AD"
    assert language_variants("ita_ad") == language_variants("ita")


def test_audio_description_title():
    assert track_title_and_iso_lang({"language": "it-it", "ad": True}) == ("Italian [AD]", "ita")
    assert track_title_and_iso_lang({"name": "ita_ad"}) == ("Italian [AD]", "ita")
