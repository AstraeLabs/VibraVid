import pytest

from VibraVid.core.drm.cdm.local import local_cdm
from VibraVid.setup import device_install
from VibraVid.setup.device_install import resolve_cdm, validate_cdm_settings


@pytest.fixture
def registries(monkeypatch):
    state = {"local": {}, "remote": {}}
    monkeypatch.setattr(device_install, "get_local_cdm_registry", lambda: state["local"])
    monkeypatch.setattr(device_install, "get_remote_cdm_registry", lambda: state["remote"])
    return state


def _identity(tmp_path):
    for name in local_cdm.PLAYREADY_IDENTITY_FILES:
        (tmp_path / name).write_bytes(b"x")
    return str(tmp_path)


def _widevine_identity(tmp_path):
    (tmp_path / "metadata.yml").write_text("wvd:\n  device_type: ANDROID\n  security_level: 3\n")
    (tmp_path / "client_id.bin").write_bytes(b"x")
    (tmp_path / "private_key.der").write_bytes(b"x")
    return str(tmp_path)


def test_validate_requires_path():
    assert "'path' is required" in local_cdm.validate_local_cdm({"system": "playready"})


def test_validate_accepts_widevine(tmp_path):
    assert local_cdm.validate_local_cdm({"system": "widevine", "path": _widevine_identity(tmp_path)}) is None


def test_validate_rejects_unknown_system():
    assert "not supported" in local_cdm.validate_local_cdm({"system": "fairplay", "path": "x"})


def test_validate_folder_lists_missing_files(tmp_path):
    (tmp_path / "extracted.cert").write_bytes(b"x")
    problem = local_cdm.validate_local_cdm({"path": str(tmp_path)})
    assert "extracted.enc.plain" in problem and "extracted.cert" not in problem


def test_validate_widevine_folder_lists_missing_files(tmp_path):
    (tmp_path / "client_id.bin").write_bytes(b"x")
    problem = local_cdm.validate_local_cdm({"system": "widevine", "path": str(tmp_path)})
    assert "metadata.yml" in problem and "private_key.der/.pem" in problem and "client_id.bin" not in problem


def test_validate_complete_folder_and_prd(tmp_path):
    assert local_cdm.validate_local_cdm({"path": _identity(tmp_path)}) is None
    prd = tmp_path / "my.prd"
    prd.write_bytes(b"x")
    assert local_cdm.validate_local_cdm({"path": str(prd)}) is None


def test_validate_complete_widevine_folder_and_wvd(tmp_path):
    assert local_cdm.validate_local_cdm({"system": "widevine", "path": _widevine_identity(tmp_path)}) is None
    wvd = tmp_path / "my.wvd"
    wvd.write_bytes(b"x")
    assert local_cdm.validate_local_cdm({"system": "widevine", "path": str(wvd)}) is None


def test_loader_is_called_with_path(tmp_path):
    loader = tmp_path / "loader.py"
    loader.write_text("def load_device(path=None):\n    return ('device', path)\n")
    assert local_cdm.build_local_device({"id": "x", "loader": str(loader), "path": str(tmp_path)}) == ("device", str(tmp_path))


def test_build_local_device_playready_folder(tmp_path, monkeypatch):
    import pyplayready.device as pr_device

    captured = {}

    class FakeDevice:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(pr_device, "Device", FakeDevice)
    local_cdm.build_local_device({"system": "playready", "path": _identity(tmp_path)})
    assert captured == {"group_key": None, "encryption_key": b"x", "signing_key": b"x", "group_certificate": b"x"}


def test_build_local_device_widevine_folder(tmp_path, monkeypatch):
    import pywidevine.device as wv_device

    captured = {}

    class FakeDevice:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(wv_device, "Device", FakeDevice)
    local_cdm.build_local_device({"system": "widevine", "path": _widevine_identity(tmp_path)})
    assert captured["type_"] == wv_device.DeviceTypes.ANDROID
    assert captured["security_level"] == 3
    assert captured["private_key"] == b"x" and captured["client_id"] == b"x"


def test_resolve_picks_named_local_cdm(registries, tmp_path):
    registries["local"]["edge"] = {"path": _identity(tmp_path)}
    chosen = resolve_cdm(None)  # nothing set: search still runs, but a named entry below wins
    assert chosen.playready_local is None

    from VibraVid.core.ui.tracker import context_tracker

    context_tracker.cdm_override = ["edge"]
    try:
        chosen = resolve_cdm(None)
    finally:
        context_tracker.cdm_override = None
    assert chosen.playready_local["id"] == "edge" and chosen.prd_path is None


def test_resolve_picks_named_local_cdm_widevine(registries, tmp_path):
    registries["local"]["l3"] = {"system": "widevine", "path": _widevine_identity(tmp_path)}

    from VibraVid.core.ui.tracker import context_tracker

    context_tracker.cdm_override = ["l3"]
    try:
        chosen = resolve_cdm(None)
    finally:
        context_tracker.cdm_override = None
    assert chosen.widevine_local["id"] == "l3" and chosen.wvd_path is None


def test_resolve_broken_local_cdm_stops(registries):
    from VibraVid.core.ui.tracker import context_tracker

    registries["local"]["edge"] = {"path": "C:/does/not/exist"}
    context_tracker.cdm_override = ["edge"]
    try:
        with pytest.raises(FileNotFoundError):
            resolve_cdm(None)
    finally:
        context_tracker.cdm_override = None


def test_validate_settings_knows_local_ids_and_flags_duplicates(registries, tmp_path):
    registries["local"]["edge"] = {"path": _identity(tmp_path)}
    about_edge = lambda: [p for p in validate_cdm_settings(["edge"]) if "'edge'" in p]  # login.toml of the machine may add unrelated problems
    assert about_edge() == []
    registries["remote"]["edge"] = {"host": "h"}
    assert any("both DRM.remote_cdm and DRM.local_cdm" in p for p in about_edge())


def test_validate_settings_knows_local_widevine_ids(registries, tmp_path):
    registries["local"]["l3"] = {"system": "widevine", "path": _widevine_identity(tmp_path)}
    about_l3 = lambda: [p for p in validate_cdm_settings(["l3"]) if "'l3'" in p]
    assert about_l3() == []
