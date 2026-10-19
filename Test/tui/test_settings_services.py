# 19.10.26

import pytest
from textual.app import App
from textual.widgets import Input, ListView, Static

from VibraVid.tui.screens.settings import SettingsScreen, service_is_set_up
from VibraVid.utils import config_manager


@pytest.fixture
def fake_login_data(monkeypatch):
    data = {
        "Provider": {"tmdb": "key123", "tvdb": ""},
        "appletv": {"cookies": "[paste here]", "cdm": "mycdm", "args": ""},
        "chili": {"email": "me@example.com", "password": "secret", "country": "IT", "cdm": "", "args": ""},
    }
    monkeypatch.setattr(config_manager, "_login_data", data)
    monkeypatch.setattr(config_manager, "save_login", lambda: None)
    return data


class SettingsApp(App):
    def on_mount(self):
        self.push_screen(SettingsScreen())


def test_a_service_is_set_up_only_by_a_credential():
    assert service_is_set_up({"email": "a@b.c", "password": "", "cdm": ""})
    assert service_is_set_up({"cookies": '{"a": "b"}'})
    assert not service_is_set_up({"cookies": "[paste here]", "cdm": "mycdm", "args": "--quality 1080"})
    assert not service_is_set_up({"device_type": "chrome", "country": "IT", "cdm": ""})
    assert not service_is_set_up({})


@pytest.mark.anyio
async def test_sidebar_lists_the_services_with_their_state(fake_login_data):
    app = SettingsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        labels = {name: label.render().plain for name, label in screen._service_labels.items()}
        assert labels == {"Provider": "✓ Provider", "appletv": "· appletv", "chili": "✓ chili"}
        # the headings are not selectable
        sidebar = screen.query_one("#settings-sections", ListView)
        assert [item.disabled for item in sidebar.children if not item.id] == [True, True]


@pytest.mark.anyio
async def test_a_service_page_shows_its_own_fields_and_notes(fake_login_data):
    app = SettingsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._load_service("chili")
        await pilot.pause()

        inputs = {inp.id: inp for inp in screen.query("#settings-form Input")}
        assert set(inputs) == {f"login-chili-{k}" for k in ("email", "password", "country", "cdm", "args")}
        assert inputs["login-chili-password"].password
        notes = [str(s.render()) for s in screen.query(".field-note")]
        assert any("clear text" in n or "in chiaro" in n for n in notes)
        assert "chili" in str(screen.query_one("#section-title", Static).render())


@pytest.mark.anyio
async def test_saving_a_service_updates_its_state_in_the_sidebar(fake_login_data):
    app = SettingsApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen._load_service("appletv")
        await pilot.pause()

        screen.query_one("#login-appletv-cookies", Input).value = '{"sid": "1"}'
        screen.action_save_current_section()
        await pilot.pause()

        assert fake_login_data["appletv"]["cookies"] == '{"sid": "1"}'
        assert screen._service_labels["appletv"].render().plain == "✓ appletv"
