# 01.08.26
# ruff: noqa: E402

import os
import sys
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[2]
gui_dir = repo_root / "GUI"
for p in (str(repo_root), str(gui_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest

from searchapp.views import _shared
from VibraVid.services._base import site_extra_args
from VibraVid.utils import config_manager


def _fake_register_cli_args(parser):
    parser.add_argument("--quality", dest="quality", default=None)
    parser.add_argument("--optimize-audio", dest="optimize_audio", action="store_true")
    return ["quality", "optimize_audio"]


@pytest.fixture
def fake_site(monkeypatch):
    lazy = SimpleNamespace(get_module=lambda: SimpleNamespace(register_cli_args=_fake_register_cli_args))
    monkeypatch.setattr(site_extra_args, "load_search_functions", lambda: {"mysite_search": lazy})
    return "mysite"


def test_parse_site_extra_args_parses_known_flags(fake_site):
    result = _shared.parse_site_extra_args(fake_site, "--quality UHD --optimize-audio")
    assert result == {"quality": "UHD", "optimize_audio": True}


def test_parse_site_extra_args_empty_string_returns_empty_dict(fake_site):
    assert _shared.parse_site_extra_args(fake_site, "") == {}
    assert _shared.parse_site_extra_args(fake_site, "   ") == {}


def test_parse_site_extra_args_invalid_shlex_raises_value_error(fake_site):
    with pytest.raises(ValueError):
        _shared.parse_site_extra_args(fake_site, '--quality "unterminated')


def test_parse_site_extra_args_unknown_site_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(site_extra_args, "load_search_functions", lambda: {})
    assert _shared.parse_site_extra_args("nosuchsite", "--quality UHD") == {}


def test_resolve_persisted_site_options_reads_login_json(fake_site, monkeypatch):
    monkeypatch.setattr(
        config_manager.login,
        "get_section",
        lambda s, default=None: {"extra_args": "--quality UHD"} if s == fake_site else (default or {}),
    )
    assert _shared._resolve_persisted_site_options(fake_site) == {"quality": "UHD", "optimize_audio": False}


def test_resolve_persisted_site_options_no_extra_args_key(fake_site, monkeypatch):
    monkeypatch.setattr(
        config_manager.login,
        "get_section",
        lambda s, default=None: {"username": "foo"} if s == fake_site else (default or {}),
    )
    assert _shared._resolve_persisted_site_options(fake_site) == {}


def test_resolve_persisted_site_options_invalid_args_does_not_raise(fake_site, monkeypatch):
    monkeypatch.setattr(
        config_manager.login,
        "get_section",
        lambda s, default=None: {"extra_args": '--quality "unterminated'} if s == fake_site else (default or {}),
    )
    assert _shared._resolve_persisted_site_options(fake_site) == {}
