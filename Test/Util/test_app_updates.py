"""Offline release updater contract: nothing downloads until a user requests it."""

import hashlib
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

updater = importlib.import_module("VibraVid.utils.upload.update")


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("v1.4.10", "1.4.9", True),
        ("v1.10.0", "1.9.99", True),
        ("1.4.6", "v1.4.6.0", False),
        ("1.3.99", "1.4.0", False),
        ("Beta Build", "1.4.6", False),
    ],
)
def test_numeric_versions(latest, current, expected):
    assert updater._newer_version(latest, current) is expected


@pytest.mark.parametrize(
    ("system", "arch", "os_version", "asset_name"),
    [
        ("windows", "x64", "2022", "VibraVid_win_2022_x64.exe"),
        ("windows", "x64", "2025", "VibraVid_win_2025_x64.exe"),
        ("linux", "x64", "22.04", "VibraVid_linux_22_04_x64"),
        ("linux", "x64", "24.04", "VibraVid_linux_24_04_x64"),
        ("darwin", "x64", "15.6", "VibraVid_mac_15_x64"),
        ("darwin", "arm64", "15.5", "VibraVid_mac_15_arm64"),
        ("darwin", "arm64", "26.0", "VibraVid_mac_26_arm64"),
    ],
)
def test_exact_release_assets(system, arch, os_version, asset_name):
    names = [
        "VibraVid_win_2022_x64.exe", "VibraVid_win_2025_x64.exe",
        "VibraVid_linux_22_04_x64", "VibraVid_linux_24_04_x64",
        "VibraVid_mac_15_x64", "VibraVid_mac_15_arm64", "VibraVid_mac_26_arm64",
    ]
    assets = [{"name": name} for name in reversed(names)]
    assert updater.pick_release_asset(assets, system, arch, os_version)["name"] == asset_name


def test_asset_fallback_only_to_compatible_older_runtime():
    assets = [{"name": "VibraVid_linux_22_04_x64"}]
    assert updater.pick_release_asset(assets, "linux", "x64", "24.04") == assets[0]
    assert updater.pick_release_asset(assets, "linux", "arm64", "24.04") is None
    assert updater.pick_release_asset(assets, "linux", "x64", "26.04") is None
    assert updater.pick_release_asset(assets, "windows", "x64", "2025") is None


class _Response:
    def __init__(self, *, payload=None, chunks=(), error=None):
        self.payload = payload
        self.chunks = chunks
        self.error = error
        self.closed = False

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.error:
            raise self.error

    def iter_content(self, chunk_size):
        yield from self.chunks

    def close(self):
        self.closed = True


class _Client:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _asset(body: bytes, *, digest=None):
    return {
        "name": "VibraVid_win_2025_x64.exe",
        "browser_download_url": "https://github.com/AstraeLabs/VibraVid/releases/download/v1.4.7/VibraVid_win_2025_x64.exe",
        "size": len(body),
        "digest": digest if digest is not None else "sha256:" + hashlib.sha256(body).hexdigest(),
    }


def test_release_check_uses_cache_and_compares_versions(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config_manager, "base_path", str(tmp_path))
    monkeypatch.setattr(updater, "get_execution_mode", lambda: "installer")
    monkeypatch.setattr(updater, "_current_version", lambda: "1.4.6")
    monkeypatch.setattr(updater, "_local_platform", lambda: ("windows", "x64", "2025"))
    release = {"tag_name": "v1.4.7", "assets": [_asset(b"exe")]}
    client = _Client(_Response(payload=release))
    monkeypatch.setattr(updater, "create_client", lambda **kwargs: client)

    first = updater.check_for_updates()
    second = updater.check_for_updates()

    assert first["available"] and first["asset"] == release["assets"][0]
    assert second["available"] and len(client.calls) == 1
    assert client.calls[0][1]["timeout"] == updater._CHECK_TIMEOUT
    assert not (tmp_path / "Downloads").exists()


def test_network_failure_is_nonfatal(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config_manager, "base_path", str(tmp_path))
    monkeypatch.setattr(updater, "get_execution_mode", lambda: "source_code")
    monkeypatch.setattr(updater, "_current_version", lambda: "1.4.6")

    def broken(**kwargs):
        raise TimeoutError("offline")

    monkeypatch.setattr(updater, "create_client", broken)
    result = updater.check_for_updates(force=True)
    assert not result["available"] and "offline" in result["error"]
    assert result["asset"] is None


def test_source_and_docker_never_offer_an_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.config_manager, "base_path", str(tmp_path))
    monkeypatch.setattr(updater, "_current_version", lambda: "1.4.6")
    monkeypatch.setattr(updater, "_latest_release", lambda **kw: {"tag_name": "v1.4.7", "assets": [_asset(b"exe")]})
    for mode in ("pip", "docker", "source_code"):
        monkeypatch.setattr(updater, "get_execution_mode", lambda: mode)
        result = updater.check_for_updates()
        assert result["available"] and result["asset"] is None


def test_streamed_download_digest_and_atomic_finalization(monkeypatch, tmp_path):
    body = b"downloaded-binary" * 100
    response = _Response(chunks=(body[:13], body[13:]))
    client = _Client(response)
    monkeypatch.setattr(updater, "create_client", lambda **kw: client)
    monkeypatch.setattr(updater, "get_is_binary_installation", lambda: False)
    target = tmp_path / "release.exe"

    assert updater.download_release_asset(_asset(body), destination=target) == target
    assert target.read_bytes() == body and response.closed
    assert list(tmp_path.glob("*.part")) == []
    assert client.calls[0][1]["stream"] is True


@pytest.mark.parametrize("failure", ["digest", "network"])
def test_bad_download_cleans_partial_file(monkeypatch, tmp_path, failure):
    body = b"release"
    response = _Response(chunks=[body], error=TimeoutError("download timeout") if failure == "network" else None)
    monkeypatch.setattr(updater, "create_client", lambda **kw: _Client(response))
    monkeypatch.setattr(updater, "get_is_binary_installation", lambda: False)
    asset = _asset(body)
    if failure == "digest":
        asset["digest"] = "sha256:" + "f" * 64
    target = tmp_path / "release.exe"

    with pytest.raises((ValueError, TimeoutError)):
        updater.download_release_asset(asset, destination=target)
    assert not target.exists()
    assert list(tmp_path.glob("*.part")) == []


def test_download_rejects_external_url(monkeypatch, tmp_path):
    asset = _asset(b"exe")
    asset["browser_download_url"] = "https://evil.example/payload"
    with pytest.raises(ValueError, match="URL"):
        updater.download_release_asset(asset, destination=tmp_path / "out.exe")


def test_download_cannot_replace_running_interpreter_even_in_source_mode(monkeypatch, tmp_path):
    running = tmp_path / "vibravid.exe"
    running.write_bytes(b"running")
    monkeypatch.setattr(updater.sys, "executable", str(running))
    monkeypatch.setattr(updater, "get_is_binary_installation", lambda: False)
    with pytest.raises(ValueError, match="running executable"):
        updater.download_release_asset(_asset(b"update"), destination=running)
    assert running.read_bytes() == b"running"


@pytest.mark.parametrize("unsafe_name", ["..", "../update.exe", "evil\\update.exe"])
def test_download_rejects_unsafe_asset_name(unsafe_name):
    asset = _asset(b"update")
    asset["name"] = unsafe_name
    with pytest.raises(ValueError, match="Unsafe release asset"):
        updater.download_release_asset(asset)


def test_menu_u_downloads_only_after_selection(monkeypatch, tmp_path):
    from VibraVid.cli import run as cli

    info = {"available": True, "mode": "installer", "asset": _asset(b"exe"), "error": None}
    chosen = iter(["u", "0"])
    download_calls = []
    monkeypatch.setattr(cli, "get_user_site_selection", lambda *_: next(chosen))
    monkeypatch.setattr(cli, "download_release_asset", lambda asset: download_calls.append(asset) or tmp_path / "app.exe")

    assert cli._choose_site_or_update(SimpleNamespace(), {}, info) == "0"
    assert download_calls == [info["asset"]]


def test_enter_keeps_original_default_menu_selection(monkeypatch):
    from VibraVid.cli import run as cli

    calls = []

    def ask(*args, **kwargs):
        calls.append(kwargs)
        return kwargs["default"]

    monkeypatch.setattr(cli.msg, "ask", ask)
    assert cli.get_user_site_selection(SimpleNamespace(), {"0": ("Example", "anime")}) == "0"
    assert calls[0]["default"] == "0" and "u" in calls[0]["choices"]


def test_update_cli_flags_are_exposed():
    from VibraVid.cli.run import setup_argument_parser

    parser, _ = setup_argument_parser({})
    args = parser.parse_args(["--check-update", "--skip-update-check"])
    assert args.check_update and args.skip_update_check
