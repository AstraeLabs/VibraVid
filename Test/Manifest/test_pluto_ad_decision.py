# 13.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.services.plutotv.ads import pluto_ad_decision

_MPD_AD_MID = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT1215S">
  <Period id="6273811ac514110013447cf9-1791889787928" duration="PT600S">
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v1" bandwidth="1900000" codecs="avc1.64001f" width="960" height="540">
        <BaseURL>https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/ep-firewall/seg-v-0.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="it">
      <Representation id="a-it" bandwidth="103000" codecs="mp4a.40.2">
        <BaseURL>https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/ep-firewall/seg-a-it-0.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="en">
      <Representation id="a-en" bandwidth="103000" codecs="mp4a.40.2">
        <BaseURL>https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/ep-firewall/seg-a-en-0.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
  <Period id="610ad0808c0fe0000755a96c-1791890452828" duration="PT15S">
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v1" bandwidth="1900000" codecs="avc1.64001f" width="960" height="540">
        <BaseURL>https://siloh-ns1.plutotv.net/v1/jit/rev(1)/init(video)/b-1.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="en">
      <Representation id="a-en" bandwidth="103000" codecs="mp4a.40.2">
        <BaseURL>https://siloh-ns1.plutotv.net/v1/jit/rev(1)/init(audio)/b-1.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
  <Period id="6273811ac514110013447cf9-1791890492828" duration="PT600S">
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v1" bandwidth="1900000" codecs="avc1.64001f" width="960" height="540">
        <BaseURL>https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/ep-firewall/seg-v-2.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="it">
      <Representation id="a-it" bandwidth="103000" codecs="mp4a.40.2">
        <BaseURL>https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/ep-firewall/seg-a-it-2.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="en">
      <Representation id="a-en" bandwidth="103000" codecs="mp4a.40.2">
        <BaseURL>https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/ep-firewall/seg-a-en-2.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""

_MPD_CLEAN = """<?xml version="1.0" encoding="utf-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT1200S">
  <Period duration="PT600S">
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v1" bandwidth="1900000" codecs="avc1.64001f" width="960" height="540">
        <BaseURL>seg-v-0.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="it">
      <Representation id="a-it" bandwidth="103000" codecs="mp4a.40.2">
        <BaseURL>seg-a-it-0.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
  <Period duration="PT600S">
    <AdaptationSet contentType="video" mimeType="video/mp4">
      <Representation id="v1" bandwidth="1900000" codecs="avc1.64001f" width="960" height="540">
        <BaseURL>seg-v-1.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
    <AdaptationSet contentType="audio" mimeType="audio/mp4" lang="it">
      <Representation id="a-it" bandwidth="103000" codecs="mp4a.40.2">
        <BaseURL>seg-a-it-1.m4s</BaseURL>
      </Representation>
    </AdaptationSet>
  </Period>
</MPD>
"""

_HLS_CUE = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MAP:URI="init-main.mp4"
#EXTINF:6.0,
seg-0.ts
#EXTINF:6.0,
seg-1.ts
#EXT-X-CUE-OUT:30.0
#EXT-X-MAP:URI="init-ads.mp4"
#EXTINF:6.0,
ad-0.ts
#EXTINF:6.0,
ad-1.ts
#EXT-X-CUE-IN
#EXT-X-MAP:URI="init-main.mp4"
#EXTINF:6.0,
seg-2.ts
#EXT-X-ENDLIST
"""

_HLS_CLEAN = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXT-X-MAP:URI="init-main.mp4"
#EXTINF:6.0,
seg-0.ts
#EXT-X-MAP:URI="init-main.mp4"
#EXTINF:6.0,
seg-1.ts
#EXT-X-ENDLIST
"""


def test_dash_drops_video_only_mid_period():
    decision = pluto_ad_decision("dash", _MPD_AD_MID, None)
    assert decision.get("drop_period_idx") == [1]
    assert decision.get("reason")


def test_dash_clean_two_periods_keeps_all():
    assert pluto_ad_decision("dash", _MPD_CLEAN, None) == {}


def test_dash_single_period_and_garbage_are_noop():
    assert pluto_ad_decision("dash", "<MPD></MPD>", None) == {}
    assert pluto_ad_decision("dash", "not xml at all", None) == {}
    assert pluto_ad_decision("dash", None, None) == {}


def test_dash_ad_url_marker_drops_period():
    mpd = _MPD_CLEAN.replace("seg-v-1.m4s", "ads/preroll-1.m4s")
    decision = pluto_ad_decision("dash", mpd, None)
    assert decision.get("drop_period_idx") == [1]


def test_hls_cue_out_block_dropped():
    decision = pluto_ad_decision("hls", _HLS_CUE, None)
    assert decision.get("keep_init_urls") == ["init-main.mp4"]


def test_hls_clean_playlist_is_noop():
    assert pluto_ad_decision("hls", _HLS_CLEAN, None) == {}
    assert pluto_ad_decision("hls", "garbage", None) == {}
    assert pluto_ad_decision("hls", None, None) == {}


def test_unknown_kind_is_noop():
    assert pluto_ad_decision("ism-unknown-kind", "whatever", None) == {}


_HLS_RANGES = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:5.0,
https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/EP123_Title/hls/0-100/hls_2400-00000.ts
#EXT-X-DISCONTINUITY
#EXTINF:5.0,
https://siloh-ns1.plutotv.net/618_Pluto_TV_OandO/clip/BUM456_plutotv_ad_bumper_it_05sec/hls/0-end/hls_2400-00000.ts
#EXT-X-DISCONTINUITY
#EXTINF:5.0,
https://siloh-ns1.plutotv.net/497_Sony_Pictures/clip/EP123_Title/hls/100-200/hls_2400-00001.ts
#EXT-X-ENDLIST
"""


def test_hls_discontinuity_ad_range_dropped_by_seg_idx():
    decision = pluto_ad_decision("hls", _HLS_RANGES, None)
    assert decision.get("drop_seg_idx") == [1]
    assert "keep_init_urls" not in decision


def test_hls_single_range_without_markers_is_noop():
    assert pluto_ad_decision("hls", _HLS_CLEAN, None) == {}


# JIT-wrapped renditions percent-encode the asset path: the decision must
# decode before grouping or the dominant-group rule misfires (audio case).
_HLS_JIT = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:5.0,
https://siloh-ns1.plutotv.net/v1/jit/c(ts)/p(%2F497_Sony_Pictures%2Fclip%2FEP123_Title%2Fhls%2F0-100)/sign/aaa
#EXTINF:5.0,
https://siloh-ns1.plutotv.net/v1/jit/c(ts)/p(%2F497_Sony_Pictures%2Fclip%2FEP123_Title%2Fhls%2F0-100)/sign/bbb
#EXT-X-DISCONTINUITY
#EXTINF:5.0,
https://siloh-ns1.plutotv.net/v1/jit/c(ts)/p(%2F618_Pluto_TV_OandO%2Fclip%2FBUM456_plutotv_ad_bumper_it_05sec%2Fhls%2F0-end)/sign/ccc
#EXT-X-DISCONTINUITY
#EXTINF:5.0,
https://siloh-ns1.plutotv.net/v1/jit/c(ts)/p(%2F497_Sony_Pictures%2Fclip%2FEP123_Title%2Fhls%2F100-200)/sign/ddd
#EXT-X-ENDLIST
"""


def test_hls_jit_encoded_urls_group_and_drop_correctly():
    decision = pluto_ad_decision("hls", _HLS_JIT, None)
    assert decision.get("drop_seg_idx") == [2]


_ISM_AD = """<?xml version="1.0" encoding="utf-8"?>
<SmoothStreamingMedia MajorVersion="2" MinorVersion="0" Duration="1509000000" TimeScale="10000000">
  <StreamIndex Type="video" Url="QualityLevels({bitrate})/Fragments(video={start time})" Name="video">
    <QualityLevel Index="0" Bitrate="1900000" FourCC="H264" MaxWidth="960" MaxHeight="540" />
    <c t="0" d="50000000" r="131" />
    <c d="50000000" r="160" />
    <c d="50000000" r="8" />
  </StreamIndex>
  <StreamIndex Type="audio" Url="QualityLevels({bitrate})/Fragments(audio={start time})" Name="audio" Language="it">
    <QualityLevel Index="0" Bitrate="103000" FourCC="AACL" SamplingRate="48000" Channels="2" />
    <c t="0" d="50000000" r="131" />
    <c d="50000000" r="160" />
    <c d="50000000" r="8" />
  </StreamIndex>
  <StreamIndex Type="audio" Url="QualityLevels({bitrate})/Fragments(ad-preroll={start time})" Name="ad-preroll" Language="en">
    <QualityLevel Index="0" Bitrate="103000" FourCC="AACL" SamplingRate="48000" Channels="2" />
    <c t="0" d="50000000" r="1" />
  </StreamIndex>
</SmoothStreamingMedia>
"""

_ISM_CLEAN = """<?xml version="1.0" encoding="utf-8"?>
<SmoothStreamingMedia MajorVersion="2" MinorVersion="0" Duration="1509000000" TimeScale="10000000">
  <StreamIndex Type="video" Url="QualityLevels({bitrate})/Fragments(video={start time})" Name="video">
    <QualityLevel Index="0" Bitrate="1900000" FourCC="H264" MaxWidth="960" MaxHeight="540" />
    <c t="0" d="50000000" r="301" />
  </StreamIndex>
</SmoothStreamingMedia>
"""


def test_ism_ad_streamindex_detected():
    decision = pluto_ad_decision("ism", _ISM_AD, None)
    assert decision.get("drop_url_substrings") == ["preroll"]


def test_ism_clean_and_garbage_are_noop():
    assert pluto_ad_decision("ism", _ISM_CLEAN, None) == {}
    assert pluto_ad_decision("ism", "garbage", None) == {}
    assert pluto_ad_decision("ism", None, None) == {}
