# 17.10.26
# ruff: noqa: E402

import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for path in (ROOT, GUI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from django.core.management import call_command
from django.test import Client

from VibraVid.utils import config_manager
CONFIG_REF = (ROOT / "Conf" / "config.toml").read_text(encoding="utf-8")
LOGIN_REF = (ROOT / "Conf" / "login.toml").read_text(encoding="utf-8")

URL = "/api/save-settings/"


@pytest.fixture(scope="module")
def client():
    call_command("migrate", verbosity=0)
    return Client(HTTP_HOST="localhost")


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """Point the real config_manager at throw-away copies of the TOML files, and put it back afterwards."""
    config_file, login_file = tmp_path / "config.toml", tmp_path / "login.toml"
    config_file.write_text(CONFIG_REF, encoding="utf-8")
    login_file.write_text(LOGIN_REF, encoding="utf-8")
    original = (config_manager.config_file_path, config_manager.login_file_path)
    monkeypatch.setattr(config_manager, "config_file_path", str(config_file))
    monkeypatch.setattr(config_manager, "login_file_path", str(login_file))
    config_manager.reload_config_only()
    config_manager.reload_login_only()
    yield config_file, login_file
    config_manager.config_file_path, config_manager.login_file_path = original
    config_manager.reload_config_only()
    config_manager.reload_login_only()


def _post(client, body: dict):
    return client.post(URL, data=json.dumps(body), content_type="application/json")


def test_raw_toml_is_saved_as_typed_with_a_backup(client, conf):
    config_file, _ = conf
    new = re.sub(r"(?m)^thread_count = .*$", "thread_count = 2  # my note", CONFIG_REF)

    response = _post(client, {"file_type": "config", "content": new})

    assert response.status_code == 200 and response.json()["success"] is True
    assert "thread_count = 2  # my note" in config_file.read_text(encoding="utf-8")
    assert Path(str(config_file) + ".backup").read_text(encoding="utf-8") == CONFIG_REF
    assert config_manager.config.get_int("DOWNLOAD", "thread_count") == 2
    assert response.json()["parsed"]["DOWNLOAD"]["thread_count"] == 2


@pytest.mark.parametrize("content", ["not = = toml", "[unclosed", '{"DOWNLOAD": {}}'])
def test_invalid_toml_is_refused_and_nothing_is_written(client, conf, content):
    config_file, _ = conf

    response = _post(client, {"file_type": "config", "content": content})

    assert response.status_code == 400 and "TOML" in response.json()["error"]
    assert config_file.read_text(encoding="utf-8") == CONFIG_REF


def test_unknown_file_type_is_refused(client, conf):
    assert _post(client, {"file_type": "domains", "content": "a = 1"}).status_code == 400
    assert _post(client, {"file_type": "config"}).status_code == 400


def test_arr_patch_changes_only_arr_and_keeps_the_comments(client, conf):
    config_file, _ = conf

    response = _post(client, {"file_type": "config", "patch": {"ARR": {"enabled": True, "max_concurrent_downloads": 3}}})

    body = response.json()
    assert response.status_code == 200 and body["success"] is True
    text = config_file.read_text(encoding="utf-8")
    assert "# Sonarr / Radarr / Seerr integration" in text
    saved = tomllib.loads(text)
    assert saved["ARR"]["enabled"] is True and saved["ARR"]["max_concurrent_downloads"] == 3
    assert saved["ARR"]["sonarr"] == tomllib.loads(CONFIG_REF)["ARR"]["sonarr"]
    assert saved["DOWNLOAD"] == tomllib.loads(CONFIG_REF)["DOWNLOAD"]
    assert body["content"] == text and body["parsed"]["ARR"]["enabled"] is True
    assert config_manager.config.get_bool("ARR", "enabled") is True


def test_args_patch_sets_and_empties_per_site_options(client, conf):
    _, login_file = conf

    _post(client, {"file_type": "login", "patch": {"tubi": {"args": "--quality UHD"}}})
    assert tomllib.loads(login_file.read_text(encoding="utf-8"))["tubi"]["args"] == "--quality UHD"
    assert config_manager.login.get("tubi", "args") == "--quality UHD"

    # what the settings page sends when the options of a service are removed: the key stays, empty
    response = _post(client, {"file_type": "login", "patch": {"tubi": {"args": ""}}, "delete": []})

    assert response.json()["success"] is True
    text = login_file.read_text(encoding="utf-8")
    assert tomllib.loads(text)["tubi"]["args"] == "" and "# VibraVid - logins" in text
    assert config_manager.login.get("tubi", "args") == ""


def test_system_page_serves_the_toml_files_and_the_arr_section(client, conf):
    page = client.get("/settings/").content.decode()

    assert ">config.toml</button>" in page and ">login.toml</button>" in page
    arr = re.search(r'<script id="cn-src-arr" type="application/json">(.*?)</script>', page, re.S)
    assert arr and json.loads(arr.group(1))["provider_fallback"] == tomllib.loads(CONFIG_REF)["ARR"]["provider_fallback"]
    assert "JSON.parse(editor" not in page  # the in-page editors no longer treat the files as JSON
