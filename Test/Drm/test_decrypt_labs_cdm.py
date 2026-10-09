# 09.10.26

import base64
import sys
from pathlib import Path
from uuid import UUID

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from pywidevine.pssh import PSSH

from VibraVid.core.drm import widevine
from VibraVid.core.drm.cdm.remote.decrypt_labs_cdm import DecryptLabsRemoteCDM, build_remote_cdm

KID_A, KID_B = "aa" * 16, "bb" * 16
KEY_A, KEY_B = "11" * 16, "22" * 16
WV_PSSH = PSSH.new(system_id=PSSH.SystemId.Widevine, key_ids=[UUID(hex=KID_A)], version=1).dumps()


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self._payload


class FakeHttp:
    """Replaces the requests session: answers each POST from a per-path queue and records the calls."""
    def __init__(self, **queues):
        self.queues = {f"/{path}": list(items) for path, items in queues.items()}
        self.calls = []

    def post(self, url, json=None, timeout=None):  # noqa: ARG002
        path = "/" + url.rsplit("/", 1)[1]
        self.calls.append((path, json))
        return FakeResponse(self.queues[path].pop(0))


def _cdm(**queues) -> tuple[DecryptLabsRemoteCDM, FakeHttp]:
    cdm = DecryptLabsRemoteCDM(secret="s3cret", device_name="L3")
    cdm._http = FakeHttp(**queues)
    return cdm, cdm._http


def _cached(*pairs):
    return {"message": "success", "message_type": "cached-keys", "cached_keys": [{"kid": k, "key": v} for k, v in pairs]}


def _challenge():
    return {"message": "success", "message_type": "license-request", "challenge": base64.b64encode(b"CHAL").decode(), "session_id": "api-1"}


def test_cached_keys_that_cover_the_required_kid_skip_the_license():
    cdm, http = _cdm(**{"get-request": [_cached((KID_A, KEY_A))]})
    sid = cdm.open()
    cdm.set_required_kids([KID_A], sid)

    assert cdm.get_license_challenge(sid, PSSH(WV_PSSH)) == b""

    [key] = cdm.get_keys(sid)
    assert key.kid.hex == KID_A and key.key.hex() == KEY_A and key.type == "CONTENT"
    assert key.key_id == key.kid  # PlayReady code reads key_id
    assert len(http.calls) == 1


def test_partial_cache_requests_a_license_and_merges_without_duplicates():
    cdm, http = _cdm(
        **{
            "get-request": [_cached((KID_A, KEY_A)), _challenge()],
            "decrypt-response": [{"message": "success", "keys": [{"kid": KID_A, "key": KEY_A}, {"kid": KID_B, "key": KEY_B}]}],
        }
    )
    sid = cdm.open()
    cdm.set_required_kids([KID_A, KID_B], sid)

    assert cdm.get_license_challenge(sid, PSSH(WV_PSSH)) == b"CHAL"
    assert http.calls[1][1]["get_cached_keys_if_exists"] is False

    cdm.parse_license(sid, b"LICENSE")
    assert sorted(k.kid.hex for k in cdm.get_keys(sid)) == [KID_A, KID_B]
    assert http.calls[2][0] == "/decrypt-response"
    assert http.calls[2][1]["session_id"] == "api-1"


def test_api_error_is_raised_with_details():
    import requests

    cdm, _ = _cdm(**{"get-request": [{"message": "failure", "details": "bad key"}]})
    with pytest.raises(requests.RequestException, match="bad key"):
        cdm.get_license_challenge(cdm.open(), PSSH(WV_PSSH))


def test_playready_needs_pssh_b64_and_parses_key_string():
    cdm = DecryptLabsRemoteCDM(secret="x", device_name="SL3")
    assert cdm.is_playready
    with pytest.raises(ValueError, match="set_pssh_b64"):
        cdm._init_data(object(), None)
    assert cdm.parse_keys_response({"keys": f"--key {KID_A}:{KEY_A}\n--key {KID_B}:{KEY_B}"}) == [
        {"kid": KID_A, "key": KEY_A, "type": "CONTENT"},
        {"kid": KID_B, "key": KEY_B, "type": "CONTENT"},
    ]


def test_factory_selects_backend_and_does_not_mutate_config(monkeypatch):
    seen = {}

    class StockRemote:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr("pywidevine.remotecdm.RemoteCdm", StockRemote)

    dl_cfg = {"type": "decrypt_labs", "secret": "k", "device_name": "L1"}
    assert isinstance(build_remote_cdm(dl_cfg, "widevine"), DecryptLabsRemoteCDM)
    assert dl_cfg == {"type": "decrypt_labs", "secret": "k", "device_name": "L1"}

    pr = build_remote_cdm({"type": "decrypt_labs", "secret": "k", "device_name": "SL3"}, "playready")
    assert pr.is_playready

    wv_cfg = {"device_type": "ANDROID", "host": "http://h", "secret": "s", "device_name": "n"}
    build_remote_cdm(wv_cfg, "widevine")
    assert wv_cfg["device_type"] == "ANDROID"  # the old code replaced this with an enum in place
    assert seen["host"] == "http://h" and "type" not in seen

    with pytest.raises(ValueError, match="device type"):
        build_remote_cdm({"device_type": "TOASTER"}, "widevine")


def test_prefer_remote_cdm_wins_over_local_device_and_empty_challenge_skips_license(monkeypatch):
    http = FakeHttp(**{"get-request": [_cached((KID_A, KEY_A))]})
    real_factory = widevine.build_remote_cdm

    def factory(cfg, system):
        cdm = real_factory(cfg, system)
        cdm._http = http
        return cdm

    def no_license_post(*args, **kwargs):
        raise AssertionError("license server must not be contacted when the keys came from the cache")

    monkeypatch.setattr(widevine, "build_remote_cdm", factory)
    monkeypatch.setattr(widevine, "create_client", no_license_post)

    keys = widevine.get_widevine_keys(
        [{"pssh": WV_PSSH, "kid": KID_A, "type": "video"}],
        "https://license.test/wv",
        cdm_device_path="C:/does/not/exist.wvd",
        cdm_remote_api={"type": "decrypt_labs", "secret": "k", "device_name": "L3"},
        prefer_remote_cdm=True,
    )

    assert keys is not None
    assert keys.get_keys_list() == [f"{KID_A}:{KEY_A}"]
