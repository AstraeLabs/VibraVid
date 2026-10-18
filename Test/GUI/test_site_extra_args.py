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


def test_parse_site_extra_args_ignores_dests_the_site_does_not_define(monkeypatch):
    """A site may list dests (season, episode) that the main CLI parser defines, not its own."""
    def register(parser):
        parser.add_argument("--vcodec", dest="vcodec", default=None)
        return ["vcodec", "season", "episode"]

    lazy = SimpleNamespace(get_module=lambda: SimpleNamespace(register_cli_args=register))
    monkeypatch.setattr(site_extra_args, "load_search_functions", lambda: {"mysite_search": lazy})
    assert _shared.parse_site_extra_args("mysite", "--vcodec AVC") == {"vcodec": "AVC"}


def test_parse_site_extra_args_empty_string_returns_empty_dict(fake_site):
    assert _shared.parse_site_extra_args(fake_site, "") == {}
    assert _shared.parse_site_extra_args(fake_site, "   ") == {}


def test_parse_site_extra_args_invalid_shlex_raises_value_error(fake_site):
    with pytest.raises(ValueError):
        _shared.parse_site_extra_args(fake_site, '--quality "unterminated')


def test_parse_site_extra_args_unknown_site_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(site_extra_args, "load_search_functions", lambda: {})
    assert _shared.parse_site_extra_args("nosuchsite", "--quality UHD") == {}


def test_resolve_persisted_site_options_reads_login_toml(fake_site, monkeypatch):
    monkeypatch.setattr(
        config_manager.login,
        "get_section",
        lambda s, default=None: {"args": "--quality UHD"} if s == fake_site else (default or {}),
    )
    assert _shared._resolve_persisted_site_options(fake_site) == {"quality": "UHD", "optimize_audio": False}


def test_resolve_persisted_site_options_no_args_key(fake_site, monkeypatch):
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
        lambda s, default=None: {"args": '--quality "unterminated'} if s == fake_site else (default or {}),
    )
    assert _shared._resolve_persisted_site_options(fake_site) == {}


def _fake_login_site(monkeypatch, spec, stored):
    lazy = SimpleNamespace(get_module=lambda: SimpleNamespace(LOGIN=spec))
    monkeypatch.setattr(site_extra_args, "load_search_functions", lambda: {"mysite_search": lazy})
    monkeypatch.setattr(site_extra_args.config_manager.login, "get", lambda section, key, *a, **k: stored.get((section, key)))


def test_login_status_reports_missing_keys_without_values(monkeypatch):
    spec = {"section": "sec", "need": "required", "keys": ["a", "b"], "optional_keys": ["c"]}
    _fake_login_site(monkeypatch, spec, {("sec", "a"): "SECRET-A", ("sec", "b"): ""})

    status = site_extra_args.get_site_login_status("mysite")

    assert status["need"] == "required"
    assert status["present"] is False
    assert status["missing"] == ["b"]
    assert "SECRET-A" not in repr(status)


def test_login_status_any_of_needs_only_one(monkeypatch):
    spec = {"need": "required", "any_of": ["token", "access_token"]}
    _fake_login_site(monkeypatch, spec, {("mysite", "access_token"): "x"})
    assert site_extra_args.get_site_login_status("mysite")["present"] is True

    _fake_login_site(monkeypatch, spec, {})
    status = site_extra_args.get_site_login_status("mysite")
    assert status["present"] is False
    assert status["missing"] == ["token or access_token"]


def test_login_status_none_when_site_declares_nothing(monkeypatch):
    lazy = SimpleNamespace(get_module=lambda: SimpleNamespace())
    monkeypatch.setattr(site_extra_args, "load_search_functions", lambda: {"mysite_search": lazy})
    assert site_extra_args.get_site_login_status("mysite") is None


def _fake_login_section(monkeypatch, sections):
    lazy = SimpleNamespace(get_module=lambda: SimpleNamespace())
    monkeypatch.setattr(site_extra_args, "load_search_functions", lambda: {"mysite_search": lazy})
    login = site_extra_args.config_manager.login
    monkeypatch.setattr(login, "has_section", lambda section: section in sections)
    monkeypatch.setattr(login, "get_section", lambda section, default=None: dict(sections.get(section) or {}))


def test_login_status_from_section_empty_values_mean_not_set(monkeypatch):
    _fake_login_section(monkeypatch, {"mysite": {"email": "", "password": ""}})
    status = site_extra_args.get_site_login_status("mysite")
    assert status["present"] is False
    assert status["missing"] == ["email", "password"]


def test_login_status_from_section_any_filled_value_counts(monkeypatch):
    _fake_login_section(monkeypatch, {"mysite": {"token": "SECRET", "refresh_token": ""}})
    status = site_extra_args.get_site_login_status("mysite")
    assert status["present"] is True
    assert "SECRET" not in repr(status)


def test_login_status_from_section_ignores_non_credential_keys(monkeypatch):
    _fake_login_section(monkeypatch, {"mysite": {"cdm": "device.wvd", "args": "--quality UHD", "token": ""}})
    assert site_extra_args.get_site_login_status("mysite")["present"] is False


def test_login_status_from_section_with_only_options_has_no_login(monkeypatch):
    _fake_login_section(monkeypatch, {"mysite": {"args": "", "cdm": ""}})
    assert site_extra_args.get_site_login_status("mysite") is None


def test_login_status_from_section_with_args_and_empty_credential_still_needs_login(monkeypatch):
    _fake_login_section(monkeypatch, {"mysite": {"args": "--quality UHD", "token": ""}})
    status = site_extra_args.get_site_login_status("mysite")
    assert status["present"] is False and status["missing"] == ["token"]


def test_login_status_from_section_empty_section_has_login_but_no_keys(monkeypatch):
    _fake_login_section(monkeypatch, {"mysite": {}})
    status = site_extra_args.get_site_login_status("mysite")
    assert status is not None and status["present"] is False


def test_login_status_site_without_section_has_no_login(monkeypatch):
    _fake_login_section(monkeypatch, {"other": {"token": "x"}})
    assert site_extra_args.get_site_login_status("mysite") is None


def test_persisted_site_options_applies_args_and_restores_the_previous_options(fake_site, monkeypatch):
    from VibraVid.core.ui.tracker import context_tracker

    monkeypatch.setattr(
        config_manager.login,
        "get_section",
        lambda s, default=None: {"args": "--quality UHD"} if s == fake_site else (default or {}),
    )
    context_tracker.site_options = {"quality": "HD"}

    with site_extra_args.persisted_site_options(fake_site):
        assert context_tracker.site_options == {"quality": "UHD", "optimize_audio": False}

    assert context_tracker.site_options == {"quality": "HD"}
    context_tracker.site_options = None


def test_adapter_methods_use_the_login_args_unless_options_are_already_set(fake_site, monkeypatch):
    from searchapp.api.base import BaseStreamingAPI
    from VibraVid.core.ui.tracker import context_tracker

    monkeypatch.setattr(
        config_manager.login,
        "get_section",
        lambda s, default=None: {"args": "--quality UHD"} if s == fake_site else (default or {}),
    )

    class FakeAPI(BaseStreamingAPI):
        def __init__(self):
            super().__init__()
            self.site_name = fake_site

        def search(self, query):
            return dict(context_tracker.site_options)

        def get_series_metadata(self, media_item):
            return None

        def start_download(self, media_item, season=None, episodes=None):
            return True

    context_tracker.site_options = None
    assert FakeAPI().search("x") == {"quality": "UHD", "optimize_audio": False}
    assert not context_tracker.site_options  # restored

    context_tracker.site_options = {"quality": "from-the-caller"}  # a download task or a remote request set its own
    assert FakeAPI().search("x") == {"quality": "from-the-caller"}
    context_tracker.site_options = None


def test_reference_login_toml_ships_empty_args():
    import tomllib

    login = tomllib.loads((repo_root / "Conf" / "login.toml").read_text(encoding="utf-8"))
    shipped = {name: section["args"] for name, section in login.items() if "args" in section}

    assert shipped and set(shipped.values()) == {""}
    assert not any("extra_args" in section for section in login.values())
