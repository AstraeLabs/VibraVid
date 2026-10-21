# 21.10.26

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from VibraVid.utils.vault._url_utils import clean_license_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://atv-ps-eu.primevideo.com/playback/drm-vod/GetWidevineLicense?a=1#f", "https://atv-ps-eu.primevideo.com"),
        ("https://license.tpstreams.com/api/v1/auhf2k/drm_license/", "https://license.tpstreams.com"),
        ("https://app.tpstreams.com/api/v1/87gq3p/assets/6ZP9GUGnsXS/drm_license", "https://app.tpstreams.com"),
        ("https://license.vdocipher.com/auth4/wv/eyJvdHAiOiIyMDE2MDMxM3ZlcnNBU0Uz", "https://license.vdocipher.com"),
        ("http://192.168.0.119:5000/license?x=1", "http://192.168.0.119:5000"),
        ("://generic", "generic"),
        ("generic", "generic"),
        ("://://://generic", "generic"),
        ("://netflix", "://netflix"),
        ("", ""),
    ],
)
def test_clean_license_url(raw, expected):
    assert clean_license_url(raw) == expected
