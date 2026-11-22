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
from VibraVid.core.drm.cdm.remote.decryptlabs import DecryptLabsCdm as RemoteCdmApi
from VibraVid.core.drm.cdm.remote.remote_cdm_api import build_remote_cdm

KID_A, KID_B = "aa" * 16, "bb" * 16
KEY_A, KEY_B = "11" * 16, "22" * 16
WV_PSSH = PSSH.new(system_id=PSSH.SystemId.Widevine, key_ids=[UUID(hex=KID_A)], version=1).dumps()
TEST_HOST = "http://remote-cdm.test"


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


def _cdm(**queues) -> tuple[RemoteCdmApi, FakeHttp]:
    cdm = RemoteCdmApi(secret="s3cret", host=TEST_HOST, device_name="L3")
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
    cdm = RemoteCdmApi(secret="x", host=TEST_HOST, device_name="SL3")
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

    api_cfg = {"type": "custom_api_1", "secret": "k", "host": TEST_HOST, "device_name": "L1"}
    assert isinstance(build_remote_cdm(api_cfg, "widevine"), RemoteCdmApi)
    assert api_cfg == {"type": "custom_api_1", "secret": "k", "host": TEST_HOST, "device_name": "L1"}

    pr = build_remote_cdm({"type": "custom_api_1", "secret": "k", "host": TEST_HOST, "device_name": "SL3"}, "playready")
    assert pr.is_playready

    wv_cfg = {"device_type": "ANDROID", "host": "http://h", "secret": "s", "device_name": "n"}
    build_remote_cdm(wv_cfg, "widevine")
    assert wv_cfg["device_type"] == "ANDROID"  # the old code replaced this with an enum in place
    assert seen["host"] == "http://h" and "type" not in seen

    with pytest.raises(ValueError, match="device type"):
        build_remote_cdm({"device_type": "TOASTER"}, "widevine")


def test_remote_cdm_wins_over_local_device_and_empty_challenge_skips_license(monkeypatch):
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
        cdm_remote_api={"type": "custom_api_1", "secret": "k", "host": TEST_HOST, "device_name": "L3"},
    )

    assert keys is not None
    assert keys.get_keys_list() == [f"{KID_A}:{KEY_A}"]


def test_analchrome_remote_cdm_factory_and_endpoints(monkeypatch):
    from VibraVid.core.drm.cdm.remote import analchrome as analchrome_cdm
    from VibraVid.setup.device_install import remote_cdm_system

    assert remote_cdm_system({"id": "analchrome"}) == "widevine"
    assert not hasattr(analchrome_cdm, "DEFAULT_BRIDGE_URL")
    assert not hasattr(analchrome_cdm, "DOCKER_CONTAINER_NAME")

    calls = []

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def post(self, url, json=None, timeout=None):
            calls.append((url, json, dict(self.headers)))
            if url.endswith("/challenge"):
                return FakeResponse({"session_id": "srv-1", "challenge": "Q0hBTA=="}, 200)
            if url.endswith("/parse"):
                return FakeResponse({"status": "ok", "keys": [{"kid": KID_A, "key": KEY_A}]}, 200)
            return FakeResponse({"error": "Not found"}, 404)

    monkeypatch.setattr(analchrome_cdm.requests, "Session", FakeSession)

    cfg = {"id": "analchrome", "host": "https://cdm.example.com/", "secret": "tok"}
    cdm = build_remote_cdm(cfg, "widevine")
    assert isinstance(cdm, analchrome_cdm.AnalChromeCdm)
    assert cdm.host == "https://cdm.example.com"
    assert cdm.secret == "tok"
    assert cfg == {"id": "analchrome", "host": "https://cdm.example.com/", "secret": "tok"}

    sid = cdm.open()
    cdm.set_service_certificate(sid, None)
    assert cdm.get_license_challenge(sid, PSSH(WV_PSSH)) == b"CHAL"
    cdm.parse_license(sid, b"LIC")
    out = cdm.get_keys(sid)
    cdm.close(sid)

    assert len(out) == 1 and out[0].kid.hex == KID_A and out[0].key.hex() == KEY_A
    assert calls[0][0] == "https://cdm.example.com/challenge"
    assert calls[0][1]["certificate"] is None
    assert calls[0][2]["X-API-Key"] == "tok"
    assert calls[1][0] == "https://cdm.example.com/parse"
    assert calls[1][1]["session_id"] == "srv-1"

    with pytest.raises(ValueError, match="host"):
        build_remote_cdm({"id": "analchrome", "secret": "tok"}, "widevine")

    with pytest.raises(ValueError, match="Widevine only"):
        build_remote_cdm({"id": "analchrome"}, "playready")


def test_remote_client_is_picked_from_the_block_id():
    from VibraVid.core.drm.cdm.remote.remote_cdm_api import load_remote_client
    from VibraVid.setup.device_install import remote_cdm_system

    assert load_remote_client({"id": "decryptlabs"}).__name__.endswith(".decryptlabs")
    assert load_remote_client({"id": "AnalChrome"}).__name__.endswith(".analchrome")
    assert load_remote_client({"id": "second_account", "type": "decryptlabs"}).__name__.endswith(".decryptlabs")
    assert load_remote_client({"type": "custom_api_1"}).__name__.endswith(".decryptlabs")  # old alias
    assert load_remote_client({"id": "my_widevine"}) is None
    assert load_remote_client({"id": "remote_cdm_api"}) is None
    assert load_remote_client({"id": "../etc/passwd"}) is None
    assert load_remote_client({}) is None

    assert remote_cdm_system({"id": "decryptlabs", "device_name": "ChromeCDM"}) == "widevine"
    assert remote_cdm_system({"id": "decryptlabs", "device_name": "SL3"}) == "playready"
    assert remote_cdm_system({"id": "my_widevine", "device_type": "ANDROID"}) == "widevine"
    assert remote_cdm_system({"id": "my_playready", "device_name": "public"}) == "playready"


def test_decryptlabs_built_from_id_without_type():
    cdm = build_remote_cdm({"id": "decryptlabs", "secret": "k", "host": TEST_HOST, "device_name": "L1"}, "widevine")
    assert isinstance(cdm, RemoteCdmApi) and not cdm.is_playready

    pr = build_remote_cdm({"id": "decryptlabs", "secret": "k", "host": TEST_HOST, "device_name": "SL3"}, "playready")
    assert pr.is_playready


def test_scheme_is_sent_to_the_provider_and_device_name_is_its_old_alias():
    for kwargs, expected in (({"scheme": "L1"}, "L1"), ({"device_name": "L2"}, "L2"), ({"scheme": "L3", "device_name": "L1"}, "L3"), ({}, "ChromeCDM")):
        cdm = RemoteCdmApi(secret="s", host=TEST_HOST, **kwargs)
        assert cdm.scheme == expected and cdm.device_name == expected
        assert not cdm.is_playready

    assert RemoteCdmApi(secret="s", host=TEST_HOST, scheme="SL3").is_playready

    cdm = RemoteCdmApi(secret="s", host=TEST_HOST, scheme="L1")
    cdm._http = FakeHttp(**{"get-request": [_challenge()]})
    sid = cdm.open()
    cdm.get_license_challenge(sid, PSSH(WV_PSSH))
    assert cdm._http.calls[0][1]["scheme"] == "L1"


def test_identity_helpers_use_id_and_scheme_not_device_name():
    from VibraVid.core.drm.cdm.remote.remote_cdm_api import remote_label
    from VibraVid.setup.device_install import describe_remote_cdm, remote_cdm_system

    assert remote_label({"id": "decryptlabs", "scheme": "L3"}) == "decryptlabs L3"
    assert remote_label({"id": "analchrome"}) == "analchrome"
    assert remote_label({"device_name": "legacy"}) == "legacy"
    assert remote_label({}) == "?"

    assert remote_cdm_system({"id": "decryptlabs", "scheme": "SL3"}) == "playready"
    assert remote_cdm_system({"id": "decryptlabs", "scheme": "L1"}) == "widevine"
    assert remote_cdm_system({"id": "analchrome"}) == "widevine"  # no device_name needed

    assert describe_remote_cdm({"id": "analchrome"}) == {"name": "analchrome", "type": "CHROME", "security_level": None}
    assert describe_remote_cdm({"id": "decryptlabs", "scheme": "L1"}) == {"name": "L1", "type": "ANDROID", "security_level": 1}
    assert describe_remote_cdm({"id": "decryptlabs", "scheme": "ChromeCDM"})["type"] == "CHROME"
    assert describe_remote_cdm({"id": "decryptlabs", "scheme": "SL3"})["security_level"] == 3000


def test_analchrome_live_service_cert_negotiation_over_license_url(monkeypatch):
    from VibraVid.core.drm.cdm.remote import analchrome as analchrome_cdm

    cdm_calls = []
    lic_posts = []

    class FakeCdmSession:
        def __init__(self):
            self.headers = {}

        def post(self, url, json=None, timeout=None):
            cdm_calls.append((url, json))
            if url.endswith("/challenge"):
                if "certificate" not in json:
                    return FakeResponse({"session_id": "s-1", "challenge": base64.b64encode(b"\x08\x04").decode()}, 200)
                assert json["certificate"] == base64.b64encode(b"NAGRA-CERT").decode()
                return FakeResponse({"session_id": "s-1", "challenge": base64.b64encode(b"ENC-CHAL").decode()}, 200)
            if url.endswith("/parse"):
                return FakeResponse({"status": "ok", "keys": [{"kid": KID_A, "key": KEY_A}]}, 200)
            return FakeResponse({}, 404)

    class FakeLicenseClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, data=None):
            lic_posts.append((url, data))
            if data == b"\x08\x04":
                return type("R", (), {"status_code": 200, "headers": {}, "content": b"NAGRA-CERT"})()
            return type("R", (), {"status_code": 200, "headers": {}, "content": b"NAGRA-LIC"})()

    monkeypatch.setattr(analchrome_cdm.requests, "Session", FakeCdmSession)
    monkeypatch.setattr(widevine, "create_client", lambda headers=None: FakeLicenseClient())

    keys = widevine.get_widevine_keys(
        [{"pssh": WV_PSSH, "kid": KID_A, "type": "video"}],
        "https://license.test/nagra",
        cdm_remote_api={"id": "analchrome", "host": "https://cdm.example.com", "secret": "tok"},
    )
    assert keys is not None and keys.get_keys_list() == [f"{KID_A}:{KEY_A}"]
    assert lic_posts == [("https://license.test/nagra", b"\x08\x04"), ("https://license.test/nagra", b"ENC-CHAL")]
    assert len(cdm_calls) == 3
