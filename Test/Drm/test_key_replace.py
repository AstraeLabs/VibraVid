# 15.10.26

import logging
import sys
import threading
import time
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.drm import manager as drm_manager_mod
from VibraVid.core.drm.key_fallback import KeyFallback
from VibraVid.core.drm.manager import DRMManager
from VibraVid.utils.vault.base import BaseVault
from VibraVid.utils.vault.default import Vault_di_default

KID = "0000000005b7aee80000000000000000"
BAD = "bb6fd0935654c59d83a77963e36275b2"
GOOD = "bb6fd0935654c59d83a779b3e36275b9"
LIC = "https://netflix.com/license"
PSSH = "AAADzHBzc2gAAAAAmgTweZhAQoarkuZb4IhflQ"


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, reply):
        self.reply = reply
        self.posts = []

    def post(self, url, json=None):
        self.posts.append((url, json))
        return FakeResponse(self.reply)


def make_default_vault(reply):
    vault = Vault_di_default(name="t", base_url="")  # empty URL: no prewarm request
    vault.base_url = "https://vault.example"
    vault.session = FakeSession(reply)
    return vault


def wait_threads():
    for _ in range(100):
        if not any(t.name in ("replace-wrong-key", "report-wrong-key") for t in threading.enumerate()):
            return
        time.sleep(0.05)


# ── Default vault client ─────────────────────────────────────────────────────────────────


def test_replace_key_payload_and_result():
    vault = make_default_vault({"replaced": 1})
    assert vault.replace_key(KID, GOOD, BAD, LIC, PSSH) is True

    url, body = vault.session.posts[0]
    assert url == "https://vault.example/save-keys"
    assert body["license_url"] == LIC and body["pssh"] == PSSH
    assert (body["kid"], body["key"], body["replaces"]) == (KID, GOOD, BAD)


@pytest.mark.parametrize(("reply", "expected"), [({"added": 1}, True), ({"unchanged": 1}, True), ({"replaced": 0, "locked": 1}, False), (None, False)])
def test_replace_key_result_mapping(reply, expected, caplog):
    vault = make_default_vault(reply)
    vault._post = lambda endpoint, payload: reply
    with caplog.at_level(logging.WARNING):
        assert vault.replace_key(KID, GOOD, BAD, LIC, PSSH) is expected
    assert ("not replaced" in caplog.text) == bool(reply and reply.get("locked"))


def test_replace_key_tells_an_old_server_apart(caplog):
    vault = make_default_vault({"locked": 1})  # an old worker has no "replaced" counter at all
    with caplog.at_level(logging.WARNING):
        assert vault.replace_key(KID, GOOD, BAD, LIC, PSSH) is False
    assert "too old to replace keys" in caplog.text


def test_report_and_replace_target_the_row_the_key_came_from():
    vault = make_default_vault({"warnings_recorded": 1})
    vault._flag_keys([{"kid": KID, "key": BAD, "license_url": "https://other.example/lic", "pssh": "OTHERPSSH", "warning_count": 0}], LIC)

    vault.report_wrong_key(KID, BAD, LIC, PSSH)
    vault.session.reply = {"replaced": 1}
    vault.replace_key(KID, GOOD, BAD, LIC, PSSH)

    for _, body in vault.session.posts:
        assert body["license_url"] == "https://other.example/lic" and body["pssh"] == "OTHERPSSH"


def test_origin_ignored_when_the_key_differs():
    vault = make_default_vault({"warnings_recorded": 1})
    vault._flag_keys([{"kid": KID, "key": GOOD, "license_url": "https://other.example/lic", "pssh": "OTHERPSSH", "warning_count": 0}], LIC)
    vault.report_wrong_key(KID, BAD, LIC, PSSH)
    assert vault.session.posts[0][1]["license_url"] == LIC and vault.session.posts[0][1]["pssh"] == PSSH


def test_set_keys_warns_when_locked(caplog):
    vault = make_default_vault({"added": 0, "locked": 1})
    with caplog.at_level(logging.WARNING):
        assert vault.set_keys([f"{KID}:{GOOD}"], LIC, PSSH) == 0
    assert "already saved for the same KID" in caplog.text


def test_base_vault_replace_falls_back_to_plain_save():
    class Plain(BaseVault):
        stored = []

        @property
        def is_writable(self):
            return True

        def set_keys(self, keys_list, license_url, pssh=None, kid_to_label=None):
            self.stored.extend(keys_list)
            return len(keys_list)

    vault = Plain(name="plain")
    assert vault.replace_key(KID, GOOD, BAD, LIC, PSSH) is True
    assert vault.stored == [f"{KID}:{GOOD}"]
    assert BaseVault(name="ro").replace_key(KID, GOOD, BAD, LIC, PSSH) is False


# ── DRMManager.replace_wrong_key + KeyFallback ───────────────────────────────────────────


class FakeVault(BaseVault):
    def __init__(self, name, keys=(), writable=True, events=None):
        super().__init__(name=name)
        self.keys, self.writable, self.events = list(keys), writable, events if events is not None else []
        self.replaced, self.reported, self.stored = [], [], []

    @property
    def is_connected(self):
        return True

    @property
    def is_writable(self):
        return self.writable

    def get_keys_by_kids(self, license_url, kids, pssh=None):
        return [k for k in self.keys if k.split(":")[0] in kids]

    def set_keys(self, keys_list, license_url, pssh=None, kid_to_label=None):
        self.stored.extend(keys_list)
        return len(keys_list)

    def report_wrong_key(self, kid, key, license_url, pssh=None):
        self.events.append(("report", self.name, key))
        self.reported.append(f"{kid}:{key}")
        return True

    def replace_key(self, kid, new_key, old_key, license_url, pssh=None):
        self.events.append(("replace", self.name, new_key))
        self.replaced.append((kid, new_key, old_key, license_url, pssh))
        return True


def make_manager(vaults):
    mgr = DRMManager.__new__(DRMManager)
    mgr._vaults = list(vaults)
    mgr.cdm_spec = lambda drm_type, **kw: ((lambda *a, **k: None), {})
    return mgr


def test_replace_wrong_key_skips_source_and_read_only_and_waits_for_report():
    events = []
    claudio = FakeVault("claudio", [f"{KID}:{BAD}"], events=events)
    source = FakeVault("source", [f"{KID}:{GOOD}"], events=events)
    read_only = FakeVault("ro", writable=False, events=events)
    mgr = make_manager([claudio, source, read_only])

    report = threading.Thread(target=lambda: (time.sleep(0.3), events.append(("report-done",))))
    report.start()
    mgr.replace_wrong_key(KID, BAD, GOOD, "source", LIC, PSSH, wait_for=report).join()

    assert claudio.replaced == [(KID, GOOD, BAD, LIC, PSSH)]
    assert source.replaced == [] and read_only.replaced == []
    assert events == [("report-done",), ("replace", "claudio", GOOD)]


def _fallback(vaults, usable=lambda keys: GOOD in "".join(keys)):
    mgr = make_manager(vaults)
    fb = KeyFallback(mgr, "widevine", [{"kid": KID, "pssh": PSSH}], license_url=LIC)
    return mgr, fb, (lambda keys: (True, None) if usable(keys) else (False, "key is wrong"))


def test_fallback_success_reports_then_replaces_on_the_vault_that_had_the_wrong_key():
    events = []
    claudio = FakeVault("claudio", [f"{KID}:{BAD}"], events=events)
    other = FakeVault("other", [f"{KID}:{GOOD}"], events=events)
    mgr, fb, try_keys = _fallback([claudio, other])

    assert fb.recover([KID], [f"{KID}:{BAD}"], try_keys) is True
    wait_threads()

    # the report goes to every connected vault; what matters is that claudio's report comes before its replace
    on_claudio = [e for e in events if e[1] == "claudio"]
    assert [e[0] for e in on_claudio] == ["report", "replace"]
    assert on_claudio[0][2] == BAD and on_claudio[1][2] == GOOD
    assert claudio.replaced == [(KID, GOOD, BAD, fb.base_license_url, PSSH)]
    assert other.replaced == [] and claudio.stored == []
    assert KeysManager.normalize(fb.apply([f"{KID}:{BAD}"])) == [(KID, GOOD)]


def test_fallback_console_names_the_vault_with_the_wrong_key_and_the_one_with_the_right_one(monkeypatch):
    from rich.console import Console

    from VibraVid.core.drm import key_fallback as key_fallback_mod

    console = Console(record=True, width=200, force_terminal=False)
    monkeypatch.setattr(key_fallback_mod, "console", console)
    claudio = FakeVault("claudio", [f"{KID}:{BAD}"])
    other = FakeVault("other", [f"{KID}:{GOOD}"])
    mgr, fb, try_keys = _fallback([claudio, other])
    claudio.position, other.position = 1, 2

    assert fb.recover([KID], [f"{KID}:{BAD}"], try_keys) is True
    wait_threads()
    lines = console.export_text().splitlines()
    assert lines == [f"[-] Wrong key for KID {KID} | claudio [1]", f"[+] Right key for KID {KID} | other [2]"]


def test_fallback_console_when_no_key_works():
    from rich.console import Console

    from VibraVid.core.drm import key_fallback as key_fallback_mod

    console = Console(record=True, width=200, force_terminal=False)
    mp = pytest.MonkeyPatch()
    mp.setattr(key_fallback_mod, "console", console)
    try:
        mgr, fb, try_keys = _fallback([FakeVault("claudio", [f"{KID}:{BAD}"])], usable=lambda keys: False)
        assert fb.recover([KID], [f"{KID}:{BAD}"], try_keys) is False
        wait_threads()
    finally:
        mp.undo()
    assert console.export_text().splitlines()[-1] == f"[-] No working fallback key for KID {KID}"


def test_fallback_from_cdm_replaces_on_every_writable_vault(monkeypatch):
    monkeypatch.setattr(drm_manager_mod, "USE_CDM", True)
    claudio = FakeVault("claudio", [f"{KID}:{BAD}"])
    mine = FakeVault("mine", [f"{KID}:{BAD}"])
    read_only = FakeVault("ro", writable=False)
    mgr, fb, try_keys = _fallback([claudio, mine, read_only])
    mgr._cdm_extract = lambda *a, **k: [f"{KID}:{GOOD}"]

    assert fb.recover([KID], [f"{KID}:{BAD}"], try_keys) is True
    wait_threads()
    assert claudio.replaced and mine.replaced and not read_only.replaced


def test_fallback_without_a_working_key_replaces_nothing_and_keeps_reporting():
    claudio = FakeVault("claudio", [f"{KID}:{BAD}"])
    mgr, fb, try_keys = _fallback([claudio], usable=lambda keys: False)

    assert fb.recover([KID], [f"{KID}:{BAD}"], try_keys) is False
    wait_threads()
    assert claudio.replaced == [] and claudio.reported == [f"{KID}:{BAD}"]


# ── One key per KID for flux ─────────────────────────────────────────────────────────────


def test_unique_by_kid_keeps_the_last_key_like_flux():
    assert KeysManager.unique_by_kid([(KID, BAD), (KID, GOOD)]) == [(KID, GOOD)]
    assert KeysManager.unique_by_kid([(KID, GOOD), ("ab" * 16, BAD), (KID, BAD)]) == [("ab" * 16, BAD), (KID, BAD)]


def test_unique_by_kid_leaves_placeholder_and_zero_kid_candidates():
    pairs = [("1", BAD), ("1", GOOD), ("0" * 32, BAD), ("0" * 32, GOOD)]
    assert KeysManager.unique_by_kid(pairs) == pairs
