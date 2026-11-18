# 05.10.26

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.downloader.dash import DASH_Downloader
from VibraVid.core.downloader.hls import HLS_Downloader
from VibraVid.core.downloader.ism import ISM_Downloader
from VibraVid.core.drm.system import DRMType

LICENSE_URL = "https://license.test/wv"
LICENSE_HEADERS = {"X-Lic": "1"}
LICENSE_DATA = {"extra": "data"}
CERT = "Y2VydA=="


def _request_fn(challenge, headers):  # pragma: no cover - identity only matters
    return b""


WV = [{"pssh": "wvpssh", "kid": "aa" * 16, "type": "Widevine"}]
PR = [{"pssh": "prpssh", "kid": "bb" * 16, "type": "PlayReady"}]
FP = [{"uri": "skd://x", "kid": "cc" * 16, "type": "FairPlay"}]


class FakeManager:
    """Mirrors the DRMManager signatures so positional and keyword calls normalize to the same record."""

    def __init__(self, wv=("wv:key",), pr=("pr:key",), fp=("fp:key",), boom=()):
        self.calls: list[tuple[str, dict]] = []
        self._ret = {"wv": wv, "pr": pr, "fp": fp}
        self._boom = set(boom)

    def _do(self, name, record):
        self.calls.append((name, record))
        if name in self._boom:
            raise RuntimeError(f"{name} boom")
        value = self._ret[name]
        return list(value) if value is not None else None

    def get_wv_keys(self, pssh_list, license_url, license_data=None, license_certificate=None, headers=None, key=None, license_request_fn=None):
        return self._do("wv", dict(pssh_list=pssh_list, license_url=license_url, license_data=license_data,
                                   license_certificate=license_certificate, headers=headers, key=key,
                                   license_request_fn=license_request_fn))

    def get_pr_keys(self, pssh_list, license_url, headers=None, key=None, license_data=None, license_request_fn=None):
        return self._do("pr", dict(pssh_list=pssh_list, license_url=license_url, headers=headers, key=key,
                                   license_data=license_data, license_request_fn=license_request_fn))

    def get_fp_keys(self, pssh_list, license_url, key=None):
        return self._do("fp", dict(pssh_list=pssh_list, license_url=license_url, key=key))


def _stub(manager, preference=DRMType.WIDEVINE, key=None, request_fn=_request_fn):
    return SimpleNamespace(
        drm_manager=manager,
        drm_preference=preference,
        license_url=LICENSE_URL,
        license_headers=LICENSE_HEADERS,
        license_data=LICENSE_DATA,
        license_certificate=CERT,
        license_request_fn=request_fn,
        key=key,
    )


def _fetch(cls, stub, psshs):
    return cls._fetch_keys(stub, psshs)


CLASSES = [HLS_Downloader, DASH_Downloader, ISM_Downloader]
IDS = ["hls", "dash", "ism"]


@pytest.mark.parametrize("cls", CLASSES, ids=IDS)
def test_widevine_preferred_and_present(cls):
    mgr = FakeManager()
    keys = _fetch(cls, _stub(mgr), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: PR, DRMType.FAIRPLAY: []})
    assert keys == ["wv:key"]
    assert [name for name, _ in mgr.calls] == ["wv"]
    call = mgr.calls[0][1]
    assert call["pssh_list"] == WV
    assert call["license_url"] == LICENSE_URL
    assert call["license_data"] == LICENSE_DATA
    assert call["license_certificate"] == CERT
    assert call["headers"] == LICENSE_HEADERS
    assert call["key"] is None


@pytest.mark.parametrize("cls", CLASSES, ids=IDS)
def test_playready_preferred_and_present(cls):
    mgr = FakeManager()
    keys = _fetch(cls, _stub(mgr, preference=DRMType.PLAYREADY), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: PR, DRMType.FAIRPLAY: []})
    assert keys == ["pr:key"]
    assert [name for name, _ in mgr.calls] == ["pr"]
    call = mgr.calls[0][1]
    assert call["pssh_list"] == PR
    assert call["license_url"] == LICENSE_URL
    assert call["headers"] == LICENSE_HEADERS
    assert call["license_data"] == LICENSE_DATA


@pytest.mark.parametrize("cls", [HLS_Downloader, DASH_Downloader], ids=["hls", "dash"])
def test_license_request_fn_is_forwarded(cls):
    mgr = FakeManager()
    _fetch(cls, _stub(mgr), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []})
    assert mgr.calls[0][1]["license_request_fn"] is _request_fn
    mgr = FakeManager()
    _fetch(cls, _stub(mgr, preference=DRMType.PLAYREADY), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: PR, DRMType.FAIRPLAY: []})
    assert mgr.calls[0][1]["license_request_fn"] is _request_fn


def test_ism_never_forwards_license_request_fn():
    """ISM does not pass license_request_fn today (kept as-is: sharing the dispatch must not change this)."""
    mgr = FakeManager()
    _fetch(ISM_Downloader, _stub(mgr), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: []})
    assert mgr.calls[0][1]["license_request_fn"] is None
    mgr = FakeManager()
    _fetch(ISM_Downloader, _stub(mgr, preference=DRMType.PLAYREADY), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: PR})
    assert mgr.calls[0][1]["license_request_fn"] is None


@pytest.mark.parametrize("cls", [DASH_Downloader, ISM_Downloader], ids=["dash", "ism"])
def test_cross_drm_fallback_dash_and_ism(cls):
    """Preference widevine but only PlayReady in the manifest: DASH/ISM fall back to PlayReady."""
    mgr = FakeManager()
    keys = _fetch(cls, _stub(mgr), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: PR})
    assert keys == ["pr:key"]
    assert [name for name, _ in mgr.calls] == ["pr"]
    mgr = FakeManager()
    keys = _fetch(cls, _stub(mgr, preference=DRMType.PLAYREADY), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: []})
    assert keys == ["wv:key"]
    assert [name for name, _ in mgr.calls] == ["wv"]


def test_hls_has_no_cross_drm_fallback():
    mgr = FakeManager()
    keys = _fetch(HLS_Downloader, _stub(mgr), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: PR, DRMType.FAIRPLAY: []})
    assert keys == []
    assert mgr.calls == []


def test_hls_fairplay_goes_through_get_fp_keys():
    mgr = FakeManager()
    keys = _fetch(HLS_Downloader, _stub(mgr, preference=DRMType.FAIRPLAY, key="k:v"),
                  {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: PR, DRMType.FAIRPLAY: FP})
    assert keys == ["fp:key"]
    assert mgr.calls == [("fp", dict(pssh_list=FP, license_url=LICENSE_URL, key="k:v"))]


@pytest.mark.parametrize("cls", CLASSES, ids=IDS)
def test_manual_key_is_the_final_fallback(cls):
    mgr = FakeManager(wv=None, pr=None)
    assert _fetch(cls, _stub(mgr, key="aa:bb"), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}) == ["aa:bb"]
    mgr = FakeManager(wv=[], pr=[])
    assert _fetch(cls, _stub(mgr, key=["a:b", "c:d"]), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}) == ["a:b", "c:d"]
    # no PSSH at all but a manual key: still returned, manager untouched
    mgr = FakeManager()
    assert _fetch(cls, _stub(mgr, key="x:y"), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}) == ["x:y"]
    assert mgr.calls == []


@pytest.mark.parametrize("cls", CLASSES, ids=IDS)
def test_no_pssh_and_no_key_gives_empty_list(cls):
    assert _fetch(cls, _stub(FakeManager()), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}) == []


@pytest.mark.parametrize("cls", [HLS_Downloader, ISM_Downloader], ids=["hls", "ism"])
def test_hls_and_ism_swallow_manager_errors(cls):
    mgr = FakeManager(boom=("wv",))
    assert _fetch(cls, _stub(mgr), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}) == []
    mgr = FakeManager(boom=("wv",))
    assert _fetch(cls, _stub(mgr, key="m:k"), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}) == ["m:k"]
    mgr = FakeManager(boom=("pr",))
    assert _fetch(cls, _stub(mgr, preference=DRMType.PLAYREADY), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: PR, DRMType.FAIRPLAY: []}) == []


def test_hls_swallows_fairplay_errors():
    mgr = FakeManager(boom=("fp",))
    assert _fetch(HLS_Downloader, _stub(mgr, preference=DRMType.FAIRPLAY), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: [], DRMType.FAIRPLAY: FP}) == []


def test_dash_lets_manager_errors_propagate():
    """DASH has no try/except around the fetch (kept: callers rely on the exception today)."""
    with pytest.raises(RuntimeError, match="wv boom"):
        _fetch(DASH_Downloader, _stub(FakeManager(boom=("wv",))), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: []})
    with pytest.raises(RuntimeError, match="pr boom"):
        _fetch(DASH_Downloader, _stub(FakeManager(boom=("pr",)), preference=DRMType.PLAYREADY), {DRMType.WIDEVINE: [], DRMType.PLAYREADY: PR})


# ---- DASH extra-audio MPD variant ---------------------------------------------------------------------------------

def _audio_stub(manager, collected, preference=DRMType.WIDEVINE, key=None):
    stub = _stub(manager, preference=preference, key=key)
    stub._collect_drm_from_streams = lambda streams, check_selected=True: collected
    stub._warn_drm_mismatch = lambda drm_psshs: None
    return stub


def _audio(stub, **overrides):
    return DASH_Downloader._fetch_keys_for_audio_mpd(stub, "https://audio.test/a.mpd", {}, None, ["s"], **overrides)


def test_dash_audio_widevine_uses_overrides_and_skips_license_data():
    mgr = FakeManager()
    stub = _audio_stub(mgr, {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: []})
    keys = _audio(stub, license_url="https://other.test/lic", license_hdrs={"X": "o"})
    assert keys == ["wv:key"]
    call = mgr.calls[0][1]
    assert call["license_url"] == "https://other.test/lic"
    assert call["headers"] == {"X": "o"}
    assert call["license_certificate"] == CERT
    assert call["license_data"] is None  # not forwarded for Widevine on the audio path
    assert call["license_request_fn"] is _request_fn


def test_dash_audio_falls_back_to_instance_license_when_no_overrides():
    mgr = FakeManager()
    stub = _audio_stub(mgr, {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: []})
    _audio(stub)
    call = mgr.calls[0][1]
    assert call["license_url"] == LICENSE_URL
    assert call["headers"] == LICENSE_HEADERS


def test_dash_audio_playready_forwards_license_data_and_cross_fallback():
    mgr = FakeManager()
    stub = _audio_stub(mgr, {DRMType.WIDEVINE: [], DRMType.PLAYREADY: PR})
    assert _audio(stub) == ["pr:key"]
    call = mgr.calls[0][1]
    assert call["license_data"] == LICENSE_DATA
    assert call["license_request_fn"] is _request_fn


def test_dash_audio_has_no_manual_key_fallback_and_empty_when_no_drm():
    mgr = FakeManager(wv=None)
    stub = _audio_stub(mgr, {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: []}, key="m:k")
    assert _audio(stub) == []  # manager returned nothing: no manual-key fallback on this path
    mgr = FakeManager()
    stub = _audio_stub(mgr, {DRMType.WIDEVINE: [], DRMType.PLAYREADY: []}, key="m:k")
    assert _audio(stub) == []
    assert mgr.calls == []


def test_dash_audio_lets_manager_errors_propagate():
    stub = _audio_stub(FakeManager(boom=("wv",)), {DRMType.WIDEVINE: WV, DRMType.PLAYREADY: []})
    with pytest.raises(RuntimeError, match="wv boom"):
        _audio(stub)
