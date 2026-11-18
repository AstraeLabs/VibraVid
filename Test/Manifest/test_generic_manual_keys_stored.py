# 06.10.26

from types import SimpleNamespace

import pytest

from VibraVid.core.downloader import _generic
from VibraVid.core.downloader._generic import Generic_Downloader

VIDEO_KID = "000000005a2a5c366335202020202020"
AUDIO_KID = "525281760ebc47f2aa1892fa507659a2"
VIDEO_KEY = "11" * 16
OTHER_KID = "ffffffff5a2a5c366339202020202020"
OTHER_KEY = "22" * 16


class _FakeDrm:
    def __init__(self, kid, pssh):
        self._kid, self.pssh, self.drm_type = kid, pssh, "widevine"

    def is_encrypted(self):
        return True

    def get_all_kids(self):
        return [self._kid]

    def get_pssh_for(self, _kind):
        return self.pssh


def _stream(kid, pssh):
    return SimpleNamespace(selected=True, is_external=False, drm=_FakeDrm(kid, pssh))


class _FakeManager:
    stored: list = []
    looked_up: list = []
    displayed: list = []

    def resolve_flat_key(self, kid, pssh, manual_key, drm_type="mp4"):
        type(self).looked_up.append(kid)
        return None

    def _store_keys(self, keys_list, drm_type="manual", base_license_url="generic", pssh_val=None, kid_to_label=None, source=None):
        type(self).stored.append((list(keys_list), drm_type, base_license_url, pssh_val, source))

    def _display_keys(self, resolved, *args, **kwargs):
        type(self).displayed.append(list(resolved))


@pytest.fixture
def fake_manager(monkeypatch):
    _FakeManager.stored, _FakeManager.looked_up, _FakeManager.displayed = [], [], []
    monkeypatch.setattr(_generic, "DRMManager", _FakeManager)
    return _FakeManager


def _downloader(pooled_keys):
    d = object.__new__(Generic_Downloader)
    d._pooled_keys = list(pooled_keys)
    video = SimpleNamespace(streams=[_stream(VIDEO_KID, "PSSH-VIDEO")])
    audio = SimpleNamespace(streams=[_stream(AUDIO_KID, "PSSH-AUDIO")])
    d._active = [(video, {}), (audio, {})]
    return d


def test_manual_key_for_a_manifest_kid_is_stored_with_its_own_pssh(fake_manager):
    d = _downloader([f"{VIDEO_KID}:{VIDEO_KEY}"])

    d._preresolve_manifest_keys()

    assert fake_manager.stored == [([f"{VIDEO_KID}:{VIDEO_KEY}"], "widevine", "generic", "PSSH-VIDEO", None)]


def test_pipe_separated_key_field_stores_only_the_keys_the_manifests_need(fake_manager):
    d = _downloader([f"{OTHER_KID}:{OTHER_KEY}|{VIDEO_KID}:{VIDEO_KEY}"])

    d._preresolve_manifest_keys()

    stored_keys = [key for call in fake_manager.stored for key in call[0]]
    assert stored_keys == [f"{VIDEO_KID}:{VIDEO_KEY}"]


def test_vault_lookup_still_happens_only_for_kids_without_a_manual_key(fake_manager):
    d = _downloader([f"{VIDEO_KID}:{VIDEO_KEY}"])

    d._preresolve_manifest_keys()

    assert fake_manager.looked_up == [AUDIO_KID]


def test_nothing_is_stored_without_manual_keys(fake_manager):
    d = _downloader([])

    d._preresolve_manifest_keys()

    assert fake_manager.stored == []


def test_a_vault_failure_does_not_break_the_download(monkeypatch, fake_manager):
    def boom(self, *args, **kwargs):
        raise RuntimeError("vault unreachable")

    monkeypatch.setattr(_FakeManager, "_store_keys", boom)
    d = _downloader([f"{VIDEO_KID}:{VIDEO_KEY}"])

    d._preresolve_manifest_keys()

    assert fake_manager.displayed == [[f"{VIDEO_KID}:{VIDEO_KEY}"]]
