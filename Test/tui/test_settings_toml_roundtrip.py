# 17.10.26

import re
import tomllib
from pathlib import Path

import pytest
from textual.app import App

from VibraVid.tui.screens.settings import SECTIONS, SettingsScreen
from VibraVid.utils import config_manager

CONFIG_REF = (Path(__file__).resolve().parents[2] / "Conf" / "config.toml").read_text(encoding="utf-8")
LOGIN_REF = (Path(__file__).resolve().parents[2] / "Conf" / "login.toml").read_text(encoding="utf-8")
# a checkout can hold a real Mediaset session in Conf/login.toml: the test needs the shipped placeholder
LOGIN_REF = re.sub(r"(?m)^rtilogin_acd = .*$", "rtilogin_acd = '''[paste here]'''", LOGIN_REF)


@pytest.fixture
def toml_files(tmp_path, monkeypatch):
    """The real config_manager reading and writing throw-away copies of the shipped TOML files."""
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


async def _save_every_section():
    class TestApp(App):
        def on_mount(self):
            self.push_screen(SettingsScreen())

    app = TestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        for section, _ in SECTIONS:
            screen._load_section(section)
            await pilot.pause()
            screen.action_save_current_section()
            await pilot.pause()


@pytest.mark.anyio
async def test_saving_every_section_without_edits_changes_nothing(toml_files):
    config_file, login_file = toml_files

    await _save_every_section()

    assert tomllib.loads(config_file.read_text(encoding="utf-8")) == tomllib.loads(CONFIG_REF)
    assert tomllib.loads(login_file.read_text(encoding="utf-8")) == tomllib.loads(LOGIN_REF)
    assert "# Simultaneous downloads" in config_file.read_text(encoding="utf-8")
    assert "# VibraVid - logins" in login_file.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_the_mediaset_placeholder_survives_a_save_from_the_form(toml_files):
    _, login_file = toml_files

    await _save_every_section()

    assert tomllib.loads(login_file.read_text(encoding="utf-8"))["mediasetinfinity"]["rtilogin_acd"] == "[paste here]"
