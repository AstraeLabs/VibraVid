# 18.10.26

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pywidevine.pssh import PSSH

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.downloader.drm_keys import selected_video_height
from VibraVid.core.downloader.hls import HLS_Downloader
from VibraVid.core.drm import manager as drm_manager
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.drm.system import DRMType
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.setup import device_install, system
from VibraVid.utils import config_manager

# One key per quality, the way services protect HLS: every variant playlist carries its own EXT-X-KEY.
KIDS = {
    480: uuid.UUID("00000000000000000000000000000480"),
    720: uuid.UUID("00000000000000000000000000000720"),
    1080: uuid.UUID("00000000000000000000000000001080"),
}

MASTER = """#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=1200000,RESOLUTION=854x480,CODECS="avc1.4d401f,mp4a.40.2"
v480.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2"
v720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=6000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
v1080.m3u8
"""



def _variant_playlist(height: int) -> str:
    pssh = PSSH.new(system_id=PSSH.SystemId.Widevine, key_ids=[KIDS[height]], version=1).dumps()
    key = (
        f'#EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="data:text/plain;base64,{pssh}",KEYID=0x{KIDS[height].hex},'
        'KEYFORMAT="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed",KEYFORMATVERSIONS="1"'
    )
    lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:6", key, "#EXTINF:6.0,", "seg0.m4s", "#EXT-X-ENDLIST"]
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def offline_variant_playlists(monkeypatch):
    """The parser reads each variant playlist over HTTP: answer with the local ones instead, so nothing touches the network."""

    def parse_variant(self, variant_url):
        content = _variant_playlist(int(variant_url.rsplit("/", 1)[-1][1:].split(".")[0]))
        return self._parse_drm_tags(content), content

    monkeypatch.setattr(HLSParser, "parse_variant", parse_variant)
    monkeypatch.setattr(HLSParser, "_probe_init_segment", lambda *_: None)


REMOTE_L1 = {"type": "custom_api_1", "secret": "k", "device_name": "L1"}
RULES = [{"min_height": 1080, "cdm": "remote_l1"}, {"cdm": "mine.wvd"}]


class FakeSearcher:
    def search(self, _ext=None, filename=None):
        return f"/bin/{filename}" if filename == "mine.wvd" else None


@pytest.fixture
def key_requests(monkeypatch):
    """A real DRMManager for 'mysite' with the quality rules; the key function only records what CDM it was handed."""
    monkeypatch.setitem(config_manager._login_data, "mysite", {"cdm": RULES})
    monkeypatch.setitem(config_manager._config_data, "DRM", {"cdm": "search"})
    monkeypatch.setattr(device_install, "get_remote_cdm_registry", lambda: {"remote_l1": REMOTE_L1})
    monkeypatch.setattr(device_install, "DeviceSearcher", FakeSearcher)
    monkeypatch.setattr(system, "get_wvd_path", lambda: "/default/default.wvd")
    monkeypatch.setattr(system, "get_prd_path", lambda: "/default/default.prd")
    monkeypatch.setattr(DRMManager, "_build_vaults", staticmethod(lambda: []))
    monkeypatch.setattr(drm_manager, "USE_CDM", True)

    calls: list[dict] = []

    def record(pssh_list, _license_url, **kwargs):
        calls.append({"pssh_list": pssh_list, **kwargs})

    monkeypatch.setattr(drm_manager, "get_widevine_keys", record)
    return calls


def _hls_streams(selected: dict[int, bool]):
    """Real Stream objects parsed from a real master playlist; *selected* maps video height -> picked by the user."""
    streams = HLSParser("https://cdn.test/master.m3u8", {}, content=MASTER, has_drm=True).parse_streams()
    assert all(s.drm.is_encrypted() for s in streams)
    for stream in streams:
        stream.selected = selected.get(stream.height, False)
    return streams


def _download(streams):
    """What HLS_Downloader.start() does after parsing: collect the PSSH of the selected streams, then one key fetch."""
    stub = SimpleNamespace(
        has_drm=True,
        m3u8_url="https://cdn.test/master.m3u8",
        headers={},
        drm_manager=DRMManager.for_site("mysite"),
        drm_preference=DRMType.WIDEVINE,
        license_url="https://license.test/wv",
        license_headers={},
        license_data=None,
        license_certificate=None,
        license_request_fn=None,
        key=None,
    )
    drm_psshs = HLS_Downloader._collect_drm_from_streams(stub, streams)
    HLS_Downloader._fetch_keys(stub, drm_psshs, streams)
    return stub, drm_psshs


def test_the_parsed_playlist_has_encrypted_video_variants():
    streams = _hls_streams({1080: True})
    assert [s.height for s in streams] == [480, 720, 1080]
    assert all(s.drm.is_encrypted() for s in streams)


@pytest.mark.parametrize(
    ("picked", "expected_height"),
    [({1080: True}, 1080), ({720: True}, 720), ({480: True, 720: True}, 720), ({480: True, 1080: True}, 1080), ({}, 0)],
)
def test_height_is_the_tallest_selected_video(picked, expected_height):
    assert selected_video_height(_hls_streams(picked)) == expected_height


def test_picking_1080_uses_the_remote_l1_cdm(key_requests):
    stub, _ = _download(_hls_streams({1080: True}))
    assert len(key_requests) == 1  # one license session for the whole download
    assert key_requests[0]["cdm_remote_api"] == REMOTE_L1
    assert key_requests[0]["cdm_device_path"] is None
    assert key_requests[0]["cdm_origin"] == "login.toml 'mysite', min_height 1080"
    assert stub.drm_manager.widevine_remote_cdm_api == REMOTE_L1


def test_picking_720_uses_the_local_device_file(key_requests):
    _download(_hls_streams({720: True}))
    assert len(key_requests) == 1
    assert key_requests[0]["cdm_device_path"] == "/bin/mine.wvd"
    assert not key_requests[0]["cdm_remote_api"]
    assert key_requests[0]["cdm_origin"] == "login.toml 'mysite'"


def test_picking_several_variants_the_tallest_decides_and_still_one_session(key_requests):
    _download(_hls_streams({480: True, 1080: True}))
    assert len(key_requests) == 1
    assert key_requests[0]["cdm_remote_api"] == REMOTE_L1


def test_unselected_variants_do_not_change_the_choice(key_requests):
    """1080 exists in the playlist but the user picked 480: the L3 file must be used, not the L1 remote."""
    _download(_hls_streams({480: True}))
    assert key_requests[0]["cdm_device_path"] == "/bin/mine.wvd"
    assert not key_requests[0]["cdm_remote_api"]


def _kids(entries) -> set[str]:
    return {e["kid"].replace("-", "").lower() for e in entries}


def test_the_license_request_carries_the_keys_of_the_selected_variants_only(key_requests):
    _, drm_psshs = _download(_hls_streams({720: True}))
    assert _kids(drm_psshs[DRMType.WIDEVINE]) == {KIDS[720].hex}
    assert key_requests[0]["pssh_list"] == drm_psshs[DRMType.WIDEVINE]


def test_two_selected_variants_are_licensed_together_by_the_same_cdm(key_requests):
    _, drm_psshs = _download(_hls_streams({480: True, 1080: True}))
    assert _kids(drm_psshs[DRMType.WIDEVINE]) == {KIDS[480].hex, KIDS[1080].hex}
    assert len(key_requests) == 1 and key_requests[0]["cdm_remote_api"] == REMOTE_L1  # one session: the tallest one decides for both keys


def test_the_key_fallback_reuses_the_cdm_chosen_for_this_download(key_requests):
    """_attach_key_fallback hands the same manager to the mid-download retry, so it can't fall back to another CDM."""
    stub, drm_psshs = _download(_hls_streams({1080: True}))
    assert stub.drm_manager.cdm_spec("widevine")[1]["cdm_remote_api"] == REMOTE_L1
    assert drm_psshs[DRMType.WIDEVINE]
    assert len(key_requests) == 1
