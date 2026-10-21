# 21.10.26
# ruff: noqa: E402

"""GUI side of ``hidden_services``: no adapter registered, ARR skips the provider."""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
gui_dir = repo_root / "GUI"
for p in (str(repo_root), str(gui_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from searchapp import api
from searchapp.arr.downloader_service import ArrDownloaderService


def _reload_registry():
    api._INITIALIZED = False
    api._initialize_registry()


@pytest.fixture
def hide(monkeypatch):
    """Hide services through the env var, rebuild the registry as the settings page does, and restore it afterwards."""

    def _hide(*names):
        monkeypatch.setenv("VIBRAVID_HIDDEN_SITES", ",".join(names))
        _reload_registry()

    yield _hide
    monkeypatch.delenv("VIBRAVID_HIDDEN_SITES", raising=False)
    _reload_registry()


def test_a_hidden_service_has_no_adapter_and_comes_back_when_shown(hide):
    assert api.is_site_available("aurora")

    hide("aurora")
    assert "aurora" not in api.get_available_sites()
    assert not api.is_site_available("aurora")
    assert not api.is_site_available("nove")  # a legacy alias of the hidden service
    with pytest.raises(ValueError):
        api.get_api("aurora")
    assert "streamingcommunity" in api.get_available_sites()  # the others are untouched

    hide()
    assert api.is_site_available("aurora")


def test_arr_provider_order_skips_hidden_services_and_duplicate_aliases(hide, monkeypatch):
    from VibraVid.utils import config_manager

    real_get_list = config_manager.config.get_list

    def get_list(section, key, *args, **kwargs):
        if (section, key) == ("ARR", "provider_fallback"):
            return ["streamingcommunity", "tubitv", "aurora", "nove", "realtime"]
        return real_get_list(section, key, *args, **kwargs)

    monkeypatch.setattr(config_manager.config, "get_list", get_list)
    svc = ArrDownloaderService(None, None)

    assert svc._provider_order("") == ["streamingcommunity", "tubitv", "aurora"]  # legacy names collapse onto aurora

    hide("tubitv", "aurora")
    assert svc._provider_order("") == ["streamingcommunity"]
    assert svc._provider_order("nove") == ["streamingcommunity"]  # a hidden primary provider is dropped too


def test_arr_never_tries_a_hidden_provider_even_with_the_stock_fallback_list(hide):
    hide("streamingcommunity")

    order = ArrDownloaderService(None, None)._provider_order("aurora")

    assert order[0] == "aurora"
    assert "streamingcommunity" not in order
