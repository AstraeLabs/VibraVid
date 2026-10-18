# 01.08.26

import pytest
from textual.app import App

from VibraVid.tui.screens.settings import SettingsScreen
from VibraVid.utils import config_manager


@pytest.fixture
def fake_login_data(monkeypatch):
    data = {
        "mysite": {
            "cdm": ["custom.wvd", "custom.prd"],
            "username": "foo",
            "args": {"quality": "UHD"},
        }
    }
    monkeypatch.setattr(config_manager, "_login_data", data)
    monkeypatch.setattr(config_manager, "save_login", lambda: None)
    return data


async def _save_login_section():
    """Push SettingsScreen, load LOGIN, save without touching any field."""

    class TestApp(App):
        def on_mount(self):
            self.push_screen(SettingsScreen())

    app = TestApp()
    async with app.run_test() as pilot:
        screen = app.screen
        screen._load_section("LOGIN")
        await pilot.pause()

        screen.action_save_current_section()
        await pilot.pause()


@pytest.mark.anyio
async def test_login_list_value_round_trips_unchanged(fake_login_data):
    await _save_login_section()

    assert fake_login_data["mysite"]["cdm"] == ["custom.wvd", "custom.prd"]
    assert isinstance(fake_login_data["mysite"]["cdm"], list)


@pytest.mark.anyio
async def test_login_dict_value_round_trips_unchanged(fake_login_data):
    await _save_login_section()

    assert fake_login_data["mysite"]["args"] == {"quality": "UHD"}


@pytest.mark.anyio
async def test_login_string_value_round_trips_unchanged(fake_login_data):
    await _save_login_section()

    assert fake_login_data["mysite"]["username"] == "foo"
