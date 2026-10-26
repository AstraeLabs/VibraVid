# 26.10.26
# Deprecated

"""Migration runs without importing the application, accessing the network, or executing hooks."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import tomlkit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from VibraVid import migrate_config as migration


def write_json(directory, name, data):
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def read_toml(directory, name):
    return tomlkit.parse((directory / f"{name}.toml").read_text(encoding="utf-8")).unwrap()


def test_settings_hooks_and_new_defaults(tmp_path):
    hook = {
        "name": "Notify",
        "type": "bash",
        "path": "/hooks/after download.sh",
        "args": ["{download_path}", "quotes: '\" \\ ü"],
        "env": {"TOKEN": "secret"},
        "enabled": True,
        "continue_on_error": False,
        "timeout": None,
    }
    source = write_json(tmp_path, "config", {
        "OUTPUT": {"root_path": r"D:\My Videos"},
        "DOWNLOAD": {"thread_count": 3},
        "CODEC": {"dv_auto": False},
        "ARR": {"path_mapping": {"/remote": "/local"}, "sonarr": {"api_key": "private"}},
        "HOOKS": {"post_download": [hook]},
    })
    original = source.read_bytes()

    report = migration.migrate(tmp_path)

    saved = read_toml(tmp_path, "config")
    assert saved["OUTPUT"]["root_path"] == r"D:\My Videos"
    assert saved["DOWNLOAD"]["thread_count"] == 3
    assert saved["DOWNLOAD"]["temp_root"] == ""
    assert saved["DEFAULT"]["auto_update_binaries"] is True
    assert saved["CODEC"]["dv_auto"] is False
    assert saved["ARR"]["path_mapping"] == {"/remote": "/local"}
    assert saved["ARR"]["sonarr"]["api_key"] == "private"
    assert saved["HOOKS"]["post_download"] == [{k: v for k, v in hook.items() if v is not None}]
    assert "# Simultaneous downloads" in (tmp_path / "config.toml").read_text()
    assert source.read_bytes() == (report["backup_dir"] / "config.json").read_bytes() == original
    assert any("timeout" in warning for warning in report["warnings"])
    assert not (tmp_path / "login.toml").exists()


def test_credentials_and_renamed_keys(tmp_path):
    write_json(tmp_path, "login", {
        "cs_chrome": {"netflixid": "session", "securenetflixid": "secure"},
        "nowtvit": {"skyCEsidismesso01 ": "now"},
        "primevideo": {"session-id": "abc", "escaped": "\\\"\nü", "extra_args": "--quality 1080", "cdm": "device.wvd"},
        "mediasetinfinity": {"rtilogin_acd": {"caToken": "ca", "adminBeToken": "be", "persona": {"id": "p"}}},
        "disney": {"access_token": "access", "refresh_token": "refresh"},
        "Provider": {"tmdb": "api-secret"},
    })

    report = migration.migrate(tmp_path)

    login = read_toml(tmp_path, "login")
    assert "cs_chrome" not in login
    assert login["netflix"]["netflixid"] == "session"
    assert login["nowtvit"]["skyCEsidmesso01"] == "now"
    assert "skyCEsidismesso01 " not in login["nowtvit"]
    assert login["primevideo"]["args"] == "--quality 1080"
    assert "extra_args" not in login["primevideo"]
    assert login["primevideo"]["cdm"] == "device.wvd"
    assert json.loads(login["primevideo"]["cookies"]) == {"session-id": "abc", "escaped": "\\\"\nü"}
    assert json.loads(login["mediasetinfinity"]["rtilogin_acd"])["persona"] == {"id": "p"}
    assert login["disney"]["token"] == "access"
    assert login["disney"]["refresh_token"] == "refresh"
    assert login["Provider"]["tmdb"] == "api-secret"
    assert "api-secret" not in str(report["warnings"])


@pytest.mark.parametrize("cookies", [
    {"session": "cookie"},
    [{"name": "session", "value": "cookie", "domain": ".example.com"}],
    [{"session": "cookie"}],
    {"cookies": [{"name": "session", "value": "cookie"}]},
])
def test_cookie_exports_remain_json_values(tmp_path, cookies):
    write_json(tmp_path, "login", {"crunchyroll": {"cookies": cookies, "device_id": "device"}})
    migration.migrate(tmp_path)
    login = read_toml(tmp_path, "login")
    assert json.loads(login["crunchyroll"]["cookies"]) == cookies
    assert login["crunchyroll"]["device_id"] == "device"


def test_whole_section_cookie_array(tmp_path):
    cookies = [{"name": "session", "value": "cookie"}]
    write_json(tmp_path, "login", {"primevideo": cookies})
    migration.migrate(tmp_path)
    assert json.loads(read_toml(tmp_path, "login")["primevideo"]["cookies"]) == cookies


def test_cookie_array_for_service_with_single_cookie_template(tmp_path):
    cookies = [{"name": "st", "value": "cookie"}]
    write_json(tmp_path, "login", {"discoveryplus": cookies})
    migration.migrate(tmp_path)
    assert json.loads(read_toml(tmp_path, "login")["discoveryplus"]["cookies"]) == cookies


@pytest.mark.parametrize("cookies", ["broken secret", "42", '[{"name":"missing value"}]'])
def test_invalid_cookies_are_reported_without_printing_the_value(tmp_path, cookies):
    write_json(tmp_path, "login", {"primevideo": {"cookies": cookies}})
    report = migration.migrate(tmp_path)
    assert read_toml(tmp_path, "login")["primevideo"]["cookies"] == "[paste here]"
    assert any("invalid cookie JSON" in warning for warning in report["warnings"])
    assert cookies not in str(report["warnings"])


@pytest.mark.parametrize("prefer", [False, True])
def test_inline_remote_cdms_are_registered_without_id_collisions(tmp_path, prefer):
    write_json(tmp_path, "config", {"DRM": {
        "prefer_remote_cdm": prefer,
        "widevine": {"host": "https://example.com", "secret": "remote-secret"},
        "remote_cdm": {"legacy_widevine": {"host": "https://existing.example.com"}},
    }})
    report = migration.migrate(tmp_path)
    drm = read_toml(tmp_path, "config")["DRM"]
    assert "widevine" not in drm and "prefer_remote_cdm" not in drm
    assert drm["remote_cdm"]["legacy_widevine_"]["system"] == "widevine"
    assert drm["remote_cdm"]["legacy_widevine_"]["secret"] == "remote-secret"
    assert drm["cdm"] == (["legacy_widevine_", "search"] if prefer else ["search", "legacy_widevine_"])
    assert any("fallback" in warning for warning in report["warnings"])
    assert "remote-secret" not in str(report["warnings"])


def test_dry_run_writes_nothing_and_reports_unmigrated_settings(tmp_path):
    write_json(tmp_path, "config", {
        "DEFAULT": {"debug_track_json": True, "custom": "secret"},
        "DOWNLOAD": {"thread_count": "wrong", "select_audio": None},
        "HOOKS": {"post_run": [[None]]},
    })
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    report = migration.migrate(tmp_path, dry_run=True)
    assert report["backup_dir"] is None
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert any("removed option" in w for w in report["warnings"])
    assert any("incompatible type" in w for w in report["warnings"])
    assert any("manual review" in w for w in report["warnings"])
    assert "secret" not in str(report["warnings"])


def test_existing_toml_requires_overwrite_and_is_backed_up(tmp_path):
    write_json(tmp_path, "config", {"DOWNLOAD": {"thread_count": 2}})
    target = tmp_path / "config.toml"
    target.write_text("broken TOML [", encoding="utf-8")
    with pytest.raises(migration.MigrationError, match="--overwrite"):
        migration.migrate(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json", "config.toml"]

    report = migration.migrate(tmp_path, overwrite=True)
    assert (report["backup_dir"] / "config.toml").read_text() == "broken TOML ["
    assert read_toml(tmp_path, "config")["DOWNLOAD"]["thread_count"] == 2
    first_backup = report["backup_dir"]
    assert migration.migrate(tmp_path, overwrite=True)["backup_dir"] != first_backup


@pytest.mark.parametrize("broken", ['{"password": "do-not-print",', "[]", '{"a": NaN,'])
def test_invalid_input_prevents_both_writes(tmp_path, broken):
    write_json(tmp_path, "config", {})
    (tmp_path / "login.json").write_text(broken)
    with pytest.raises(migration.MigrationError) as exc:
        migration.migrate(tmp_path)
    assert "do-not-print" not in str(exc.value)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json", "login.json"]


def test_later_write_failure_restores_existing_targets_and_keeps_backups(tmp_path, monkeypatch):
    for name in ("config", "login"):
        write_json(tmp_path, name, {})
        (tmp_path / f"{name}.toml").write_text(f"# original {name}")
    replace = migration.os.replace

    def fail_login(source, target):
        if Path(target).name == "login.toml":
            raise OSError("write failed")
        replace(source, target)

    monkeypatch.setattr(migration.os, "replace", fail_login)
    with pytest.raises(migration.MigrationError, match="write failed"):
        migration.migrate(tmp_path, overwrite=True)
    for name in ("config", "login"):
        assert (tmp_path / f"{name}.toml").read_text() == f"# original {name}"
    backup = next(tmp_path.glob("migration-*"))
    assert (backup / "config.toml").read_text() == "# original config"
    assert not list(tmp_path.glob("*.tmp"))


def test_login_output_and_backups_are_private(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits")
    write_json(tmp_path, "login", {"tubi": {"email": "me", "password": "secret"}})
    report = migration.migrate(tmp_path)
    assert (tmp_path / "login.toml").stat().st_mode & 0o777 == 0o600
    assert report["backup_dir"].stat().st_mode & 0o777 == 0o700
    assert (report["backup_dir"] / "login.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("launcher", [["manual.py"], ["tui.py"], ["-m", "VibraVid"], ["-m", "VibraVid.migrate_config"]])
def test_launchers_run_offline_without_application_imports(tmp_path, launcher):
    conf = tmp_path / "Conf"
    conf.mkdir()
    write_json(conf, "config", {"HOOKS": {"pre_run": [{"type": "bash", "command": "exit 99"}]}})
    env = {**os.environ, "VIBRAVID_BASE_PATH": str(tmp_path)}
    result = subprocess.run([sys.executable, *launcher, "--migrate-config", "--dry-run"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "Dry run" in result.stdout
    assert sorted(p.name for p in conf.iterdir()) == ["config.json"]


def test_bootstrap_migration_does_not_import_utils(tmp_path):
    write_json(tmp_path, "config", {})
    code = """
import sys
from VibraVid.cli.bootstrap import main
main()
assert 'VibraVid.utils' not in sys.modules
assert 'VibraVid.cli.run' not in sys.modules
assert 'curl_cffi' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code, "--migrate-config", str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_no_legacy_files_is_a_clear_error(tmp_path):
    with pytest.raises(migration.MigrationError, match="No config.json or login.json"):
        migration.migrate(tmp_path)


def test_custom_defaults_are_local_and_keep_new_options(tmp_path):
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "config.toml").write_text('[DOWNLOAD]\nthread_count = 10\nnew_option = "new"\n')
    write_json(tmp_path, "config", {"DOWNLOAD": {"thread_count": 2}})
    migration.migrate(tmp_path, defaults_dir=defaults)
    assert read_toml(tmp_path, "config")["DOWNLOAD"] == {"thread_count": 2, "new_option": "new"}


def test_frozen_command_uses_bundled_defaults_and_executable_conf(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "Conf").mkdir(parents=True)
    for name in ("config", "login"):
        (bundle / "Conf" / f"{name}.toml").write_text("# bundled default\n")
    monkeypatch.delenv("VIBRAVID_BASE_PATH", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "VibraVid"))
    assert migration.default_templates_dir() == bundle / "Conf"
    assert migration.default_conf_dir() == tmp_path / "Conf"
