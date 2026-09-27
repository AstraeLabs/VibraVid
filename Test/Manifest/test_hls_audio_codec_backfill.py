# 27.09.26
# ruff: noqa: E402

import sys
from pathlib import Path
from unittest.mock import patch

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.manifest.m3u8 import HLSParser

_MASTER_MIXED_GROUP = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="DTS-X",NAME="English",LANGUAGE="en",AUTOSELECT=YES,URI="r/composite_448k_dtsx_en_PRIMARY.m3u8"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="DTS-X",NAME="Italian",LANGUAGE="it",AUTOSELECT=YES,URI="r/composite_256k_ec-3_it_PRIMARY.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=9000000,CODECS="avc1.640028,dtsx,ec-3",RESOLUTION=1920x1080,AUDIO="DTS-X"
r/composite_video_1080p.m3u8
"""

_MASTER_GENERIC_GROUP = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac-128k",NAME="English",LANGUAGE="en",AUTOSELECT=YES,URI="audio/en/index.m3u8"
#EXT-X-STREAM-INF:BANDWIDTH=2000000,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1280x720,AUDIO="aac-128k"
video/720p.m3u8
"""


def _parse(content: str):
    parser = HLSParser(m3u8_url="file:///dummy/master.m3u8", content=content)
    parser.fetch_manifest()
    with patch.object(HLSParser, "_resolve_drm", lambda self, streams, master_drm: None):
        return parser.parse_streams()


def test_dtsx_track_keeps_dtsx_codec():
    streams = _parse(_MASTER_MIXED_GROUP)
    en = next(s for s in streams if s.type == "audio" and s.language == "en")
    assert "dtsx" in en.codecs


def test_ec3_track_in_dtsx_group_is_not_mislabeled():
    """The real bug: 'it' shares GROUP-ID='DTS-X' with 'en' but its own URI says ec-3 --
    the group-wide STREAM-INF backfill would otherwise label it 'dtsx' too."""
    streams = _parse(_MASTER_MIXED_GROUP)
    it = next(s for s in streams if s.type == "audio" and s.language == "it")
    assert "ec-3" in it.codecs
    assert "dtsx" not in it.codecs


def test_generic_uri_falls_back_to_group_backfill():
    """No recognizable codec token in the URI filename: unchanged, old per-group behaviour."""
    streams = _parse(_MASTER_GENERIC_GROUP)
    en = next(s for s in streams if s.type == "audio" and s.language == "en")
    assert "mp4a" in en.codecs
