# 17.10.26

import json
import tomllib
from pathlib import Path

import pytest

from VibraVid.utils import config as config_module
from VibraVid.utils.config import (
    CONFIG_DOWNLOAD_URL,
    CONFIG_LOGIN_DOWNLOAD_URL,
    ConfigAccessor,
    ConfigManager,
)

workspace_root = Path(__file__).resolve().parents[2]

SAMPLE_CONFIG = """# main configuration
[DOWNLOAD]
# Simultaneous downloads
thread_count = 10
select_audio = "ita|eng"
delay_after_download = 0

[PROCESS]
param_video = [
    "-c:v",
    "libx265",
]
param_final = ["-c", "copy"]
mux_dtsx = false

# Sonarr / Radarr / Seerr integration
[ARR]
enabled = false

[ARR.path_mapping]

[ARR.sonarr]
url = ""
api_key = ""
"""

SAMPLE_LOGIN = """# VibraVid - logins
[crunchyroll]
cookies = '''[paste here]'''
device_id = ""

[mediasetinfinity]
rtilogin_acd = '''[paste here]'''
cdm = ""

[tubi]
email = ""
password = ""
cdm = ""
"""


def _manager(conf_dir: Path) -> ConfigManager:
    """A ConfigManager on *conf_dir* without the network side of the real constructor (domains, refresh thread)."""
    manager = object.__new__(ConfigManager)
    manager.conf_path = str(conf_dir)
    manager.config_file_path = str(conf_dir / "config.toml")
    manager.login_file_path = str(conf_dir / "login.toml")
    manager._config_data = {}
    manager._login_data = {}
    manager.cache = {}
    manager.config = ConfigAccessor(manager._config_data, manager.cache, "config", True, repair_callback=None)
    manager.login = ConfigAccessor(manager._login_data, manager.cache, "login", True)
    return manager


def _read(path) -> str:
    return Path(path).read_text(encoding="utf-8")


@pytest.fixture
def conf(tmp_path):
    """A conf directory that already holds both files, and a manager on it."""
    (tmp_path / "config.toml").write_text(SAMPLE_CONFIG, encoding="utf-8")
    (tmp_path / "login.toml").write_text(SAMPLE_LOGIN, encoding="utf-8")
    return tmp_path, _manager(tmp_path)


# --- the shipped files -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["config", "login"])
def test_shipped_files_are_valid_toml(name):
    tomllib.loads(_read(workspace_root / "Conf" / f"{name}.toml"))


def test_shipped_config_documents_vaults_remote_cdm_and_select_audio():
    text = _read(workspace_root / "Conf" / "config.toml")
    assert "[DRM.vault.default]" in text
    assert "# [DRM.vault.myvault]" in text
    assert "# [DRM.remote_cdm.my_widevine]" in text
    assert '"1ita|2eng"' in text


# --- missing files are downloaded, there is no embedded copy -----------------------------------------------------------------
def test_missing_files_are_downloaded_from_github(tmp_path):
    manager = _manager(tmp_path)
    downloads = []

    def fake_download(url, file_path):
        downloads.append(url)
        Path(file_path).write_text(SAMPLE_CONFIG if url == CONFIG_DOWNLOAD_URL else SAMPLE_LOGIN, encoding="utf-8")

    manager._download_file = fake_download
    manager._load_config()
    manager._load_login()

    assert downloads == [CONFIG_DOWNLOAD_URL, CONFIG_LOGIN_DOWNLOAD_URL]
    assert CONFIG_DOWNLOAD_URL.endswith("/Conf/config.toml") and CONFIG_LOGIN_DOWNLOAD_URL.endswith("/Conf/login.toml")
    assert manager.config.get_int("DOWNLOAD", "thread_count") == 10
    assert manager.login.get("tubi", "email") == ""


def test_existing_files_are_never_downloaded(conf):
    tmp_path, manager = conf
    manager._download_file = lambda *a: pytest.fail("an existing file must not be downloaded again")

    manager._load_config()
    manager._load_login()

    assert _read(tmp_path / "config.toml") == SAMPLE_CONFIG


def test_a_failed_download_is_a_clear_error_and_leaves_no_file(tmp_path):
    manager = _manager(tmp_path)

    def offline(url, file_path):
        raise ConnectionError("no network")

    manager._download_file = offline

    with pytest.raises(FileNotFoundError, match="config.toml"):
        manager._load_config()
    assert not (tmp_path / "config.toml").exists()


def test_download_is_written_atomically(tmp_path, monkeypatch):
    class _Response:
        status_code = 200
        content = SAMPLE_CONFIG.encode("utf-8")

    monkeypatch.setattr(config_module.requests, "get", lambda *a, **k: _Response())
    target = tmp_path / "config.toml"

    _manager(tmp_path)._download_file(CONFIG_DOWNLOAD_URL, str(target))

    assert _read(target) == SAMPLE_CONFIG
    assert not list(tmp_path.glob("*.tmp"))


def test_json_files_are_not_read_at_all(conf):
    tmp_path, manager = conf
    (tmp_path / "config.json").write_text(json.dumps({"DOWNLOAD": {"thread_count": 3}}), encoding="utf-8")
    (tmp_path / "login.json").write_text(json.dumps({"tubi": {"email": "old@example.com"}}), encoding="utf-8")

    manager._load_config()
    manager._load_login()

    assert manager.config.get_int("DOWNLOAD", "thread_count") == 10
    assert manager.login.get("tubi", "email") == ""
    assert (tmp_path / "config.json").exists() and (tmp_path / "login.json").exists()
    assert not list(tmp_path.glob("*.migrated"))


def test_a_broken_toml_is_an_error_and_is_not_overwritten(conf):
    tmp_path, manager = conf
    (tmp_path / "config.toml").write_text("[DOWNLOAD\nthread_count = ", encoding="utf-8")

    with pytest.raises(ValueError, match="parsing config TOML"):
        manager._load_config()
    assert _read(tmp_path / "config.toml") == "[DOWNLOAD\nthread_count = "


# --- saving ------------------------------------------------------------------------------------------------------------------
def test_save_config_keeps_comments_and_only_changes_what_changed(conf):
    tmp_path, manager = conf
    manager._load_config()
    manager._config_data["DOWNLOAD"]["thread_count"] = 4
    manager._config_data["PROCESS"]["param_final"] = ["-c", "copy", "-map", "0"]
    manager.save_config()

    text = _read(tmp_path / "config.toml")
    assert "# Simultaneous downloads" in text
    assert "thread_count = 4" in text
    assert 'param_video = [\n    "-c:v",' in text  # an untouched list keeps its layout
    assert tomllib.loads(text)["PROCESS"]["param_final"] == ["-c", "copy", "-map", "0"]
    assert not list(tmp_path.glob("*.tmp"))


def test_save_login_drops_removed_keys_and_ignores_none(conf):
    tmp_path, manager = conf
    manager._load_login()
    manager._login_data["tubi"]["email"] = "me@example.com"
    del manager._login_data["tubi"]["password"]
    manager._login_data["tubi"]["token"] = None  # TOML has no null
    manager._login_data["newsite"] = {"user": "x", "nested": {"a": 1}}
    manager.save_login()

    saved = tomllib.loads(_read(tmp_path / "login.toml"))
    assert saved["tubi"] == {"email": "me@example.com", "cdm": ""}
    assert saved["newsite"] == {"user": "x", "nested": {"a": 1}}
    assert "# VibraVid - logins" in _read(tmp_path / "login.toml")


def test_saving_without_a_file_writes_the_values_in_memory(tmp_path):
    manager = _manager(tmp_path)
    manager._config_data.update({"DOWNLOAD": {"thread_count": 2}})
    manager.save_config()

    assert tomllib.loads(_read(tmp_path / "config.toml")) == {"DOWNLOAD": {"thread_count": 2}}


def test_patch_file_merges_deletes_and_reloads(conf):
    _, manager = conf
    manager._load_config()
    manager._load_login()

    text = manager.patch_file("config", {"ARR": {"enabled": True, "path_mapping": {"/a": "/b"}}})
    assert "# Sonarr / Radarr / Seerr integration" in text
    assert manager.config.get_bool("ARR", "enabled") is True
    assert manager.config.get_section("ARR")["path_mapping"] == {"/a": "/b"}
    assert manager.config.get_section("ARR")["sonarr"] == {"url": "", "api_key": ""}  # the rest of the section is untouched

    manager.patch_file("login", {"tubi": {"extra_args": "--x"}})
    assert manager.login.get("tubi", "extra_args") == "--x"
    manager.patch_file("login", {}, delete=[["tubi", "extra_args"], ["nosuch", "key"]])
    assert manager.login.get("tubi", "extra_args", default=None) is None

    with pytest.raises(ValueError):
        manager.patch_file("domains", {})


# --- the login normalisation -------------------------------------------------------------------------------------------------
def _login_accessor(data: dict) -> ConfigAccessor:
    return ConfigAccessor(data, {}, "login")


def test_mediaset_template_placeholder_means_not_logged_in():
    section = tomllib.loads(SAMPLE_LOGIN)["mediasetinfinity"]
    assert section["rtilogin_acd"] == "[paste here]"

    login = _login_accessor({"mediasetinfinity": section})
    assert "rtilogin_acd" not in login.get_section("mediasetinfinity")
    assert login.get("mediasetinfinity", "adminBeToken", default=None) is None


def test_mediaset_rtilogin_acd_json_is_decoded():
    value = json.dumps({"caToken": "ca", "adminBeToken": "be", "persona": {"id": "p"}})
    section = tomllib.loads(f"[mediasetinfinity]\nrtilogin_acd = '''{value}'''\n")["mediasetinfinity"]

    login = _login_accessor({"mediasetinfinity": section})
    decoded = login.get_section("mediasetinfinity")
    assert decoded["rtilogin_acd"]["caToken"] == "ca"
    assert login.get("mediasetinfinity", "adminBeToken") == "be"


def test_literal_triple_quotes_keep_json_escapes_intact():
    raw = r'{"a": "line\nbreak", "b": "q\"uote"}'
    toml_text = f"[x]\ncookies = '''{raw}'''\n"
    assert tomllib.loads(toml_text)["x"]["cookies"] == raw

    login = _login_accessor(tomllib.loads(toml_text))
    assert login.get_section("x") == {"a": "line\nbreak", "b": 'q"uote'}


def test_every_login_placeholder_is_ignored():
    login = _login_accessor(tomllib.loads(SAMPLE_LOGIN))
    assert "cookies" not in login.get_section("crunchyroll")


# --- the repair path ---------------------------------------------------------------------------------------------------------
def test_repair_reads_the_remote_reference_as_toml(conf, monkeypatch):
    tmp_path, manager = conf
    manager._load_config()
    del manager._config_data["PROCESS"]["mux_dtsx"]

    class _Response:
        status_code = 200
        text = SAMPLE_CONFIG

    monkeypatch.setattr(config_module.requests, "get", lambda *a, **k: _Response())

    assert manager._repair_missing_config_keys() is True
    assert tomllib.loads(_read(tmp_path / "config.toml"))["PROCESS"]["mux_dtsx"] is False
