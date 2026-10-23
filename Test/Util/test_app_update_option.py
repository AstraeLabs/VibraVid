"""`[DEFAULT] auto_check_update`: opt-out of the automatic release check."""

import importlib

import pytest

updater = importlib.import_module("VibraVid.utils.upload.update")


@pytest.mark.parametrize("configured", [True, False])
def test_option_is_read_from_the_default_section(monkeypatch, configured):
    seen = {}

    def fake_get_bool(section, key, default=None):
        seen.update(section=section, key=key, default=default)
        return configured

    monkeypatch.setattr(updater.config_manager.config, "get_bool", fake_get_bool)

    assert updater.auto_check_enabled() is configured
    assert seen == {"section": "DEFAULT", "key": "auto_check_update", "default": True}


def test_explicit_check_ignores_the_option(monkeypatch):
    monkeypatch.setattr(updater.config_manager.config, "get_bool", lambda *a, **k: False)
    monkeypatch.setattr(updater, "_latest_release", lambda **kwargs: {"tag_name": "v999.0.0", "assets": []})

    assert updater.check_for_updates(force=True)["latest"] == "v999.0.0"
