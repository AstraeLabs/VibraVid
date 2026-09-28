# 28.09.26

import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.drm.system import DRMType

from VibraVid.core.downloader.dash import DASH_Downloader
from VibraVid.core.downloader.hls import HLS_Downloader
from VibraVid.core.downloader.ism import ISM_Downloader

PRIMARY_KID = "54ca8855648e44cb8b02c484ddf7ebbd"
COMPANION_KID = "74ef32ea0393483a993acb7b885ff34c"


class _FakeDRM:
    """Minimal stand-in for DRMInfo: carries one KID and one PSSH per DRM system."""

    def __init__(self, kid, pssh):
        self._kid = kid
        self._pssh = pssh

    def is_encrypted(self):
        return True

    def get_all_kids(self):
        return [self._kid]

    def get_all_drm_types(self):
        return [DRMType.PLAYREADY]

    def get_pssh_for(self, drm_type):
        return self._pssh

    def get_all_pssh_for(self, drm_type):
        return [self._pssh] if self._pssh else []

    def get_key_uri(self, drm_type, pssh):
        return None

    def get_kid_display(self):
        return self._kid


def _stream(kid, pssh, *, selected, companion=False, res="1920x1080"):
    return SimpleNamespace(
        type="video",
        selected=selected,
        dv_companion=companion,
        drm=_FakeDRM(kid, pssh),
        playlist_url=None,
        width=int(res.split("x")[0]),
        height=int(res.split("x")[1]),
        resolution=res,
        codecs="hvc1.2.4.L153.B0",
        language=None,
        is_external=False,
    )


def _collect(downloader_cls, streams):
    """Call _collect_drm_from_streams without running a real download."""
    stub = SimpleNamespace(has_drm=True, m3u8_url="https://example.test/master.m3u8", headers={})
    return downloader_cls._collect_drm_from_streams(stub, streams)


def _kids(collected, drm_type=DRMType.PLAYREADY):
    return {entry["kid"] for entry in collected[drm_type]}


def test_hls_licenses_the_dv_companion_kid():
    streams = [
        _stream(PRIMARY_KID, "UFDRPNDb", selected=True),
        _stream(COMPANION_KID, "UFDRPNDc", selected=False, companion=True, res="640x360"),
    ]
    assert _kids(_collect(HLS_Downloader, streams)) == {PRIMARY_KID, COMPANION_KID}


def test_hls_ignores_unselected_non_companion_streams():
    """Only the companion is pulled in; other unselected variants stay out."""
    streams = [
        _stream(PRIMARY_KID, "UFDRPNDb", selected=True),
        _stream(COMPANION_KID, "UFDRPNDc", selected=False, companion=True, res="640x360"),
        _stream("0" * 32, "UFDRPNDd", selected=False, res="1280x720"),
    ]
    collected = _collect(HLS_Downloader, streams)
    assert _kids(collected) == {PRIMARY_KID, COMPANION_KID}
    assert len(collected[DRMType.PLAYREADY]) == 2


def test_hls_companion_only_still_licensed():
    """No selected video at all: the companion is still a track we download."""
    streams = [_stream(COMPANION_KID, "UFDRPNDc", selected=False, companion=True, res="640x360")]
    assert _kids(_collect(HLS_Downloader, streams)) == {COMPANION_KID}


def test_dash_licenses_the_dv_companion_kid():
    streams = [
        _stream(PRIMARY_KID, "UFDRPNDb", selected=True),
        _stream(COMPANION_KID, "UFDRPNDc", selected=False, companion=True, res="640x360"),
    ]
    assert _kids(_collect(DASH_Downloader, streams)) == {PRIMARY_KID, COMPANION_KID}


def test_dash_fallback_scan_is_unchanged():
    """check_selected=False already covered every encrypted stream; it must stay that way."""
    streams = [_stream(PRIMARY_KID, "UFDRPNDb", selected=False)]
    stub = SimpleNamespace(has_drm=True, m3u8_url="https://example.test/manifest.mpd", headers={})
    collected = DASH_Downloader._collect_drm_from_streams(stub, streams, check_selected=False)
    assert _kids(collected) == {PRIMARY_KID}


def test_ism_licenses_the_dv_companion_kid():
    streams = [
        _stream(PRIMARY_KID, "UFDRPNDb", selected=True),
        _stream(COMPANION_KID, "UFDRPNDc", selected=False, companion=True, res="640x360"),
    ]
    assert _kids(_collect(ISM_Downloader, streams)) == {PRIMARY_KID, COMPANION_KID}
