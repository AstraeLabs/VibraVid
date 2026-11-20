"""Regression checks for LA7Play's public Next.js playback metadata."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from VibraVid.services.la7.client import _la7play_metadata, get_playback_info


def _html(*, drm=False, remote="/nk/content/entry/data/0/408/0_abc_0_def_2.mp4", cdn="https://cdn.example.org", old_id=None):
    video = {
        "drm": drm,
        "uploadVideo": {
            "cf_entry_id": "0_abc",
            "cf_remote_url": remote,
            "cf_old_entry_id": old_id,
        },
        "playerSettings": {"hostnameCdn": cdn},
    }
    payload = json.dumps(video, separators=(",", ":"))
    return "<script>self.__next_f.push([1," + json.dumps(payload) + "])</script>"


def test_la7play_new_video_manifest_urls():
    data = _la7play_metadata(_html())
    assert data["drm"] is None
    assert data["dash_url"] == (
        "https://cdn.example.org/local/dash/,"
        "/nk/content/entry/data/0/408/0_abc_0_def_2,.mp4.urlset/manifest.mpd"
    )
    assert "/local/hls/," in data["hls_url"]
    assert data["hls_url"].endswith(".mp4.urlset/master.m3u8")


def test_la7play_old_flavor_path_does_not_get_nk_prefix():
    data = _la7play_metadata(_html(
        remote="/content/entry/data/0/721/0_abc_0_def_1.mp4", old_id="old",
    ))
    assert "/local/dash/,/content/entry/" in data["dash_url"]
    assert "/nk/" not in data["dash_url"]


def test_la7play_drm_flag():
    assert _la7play_metadata(_html(drm=True))["drm"] == "DRM"


def test_la7play_missing_drm_or_malformed_data_fails_closed():
    assert _la7play_metadata("<html></html>") is None
    assert _la7play_metadata(_html().replace(r'\"drm\":false', r'\"something\":false')) is None
    assert _la7play_metadata(_html(cdn="http://cdn.example.org")) is None
    assert _la7play_metadata(_html(remote="../private.mp4")) is None


def test_protected_la7play_does_not_fetch_manifest():
    html = _html(drm=True)
    class Response:
        text = html
        def raise_for_status(self):
            pass
    class Client:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            if "cdn.example.org" in url:
                raise AssertionError("Protected content must not request manifest")
            return Response()
    with patch("VibraVid.services.la7.client.create_client", return_value=Client()):
        info = get_playback_info("https://www.la7.it/sample")
    assert info["drm"] == "DRM"
    assert not info["dash_url"] and not info["hls_url"]
