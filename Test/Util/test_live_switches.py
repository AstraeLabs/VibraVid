# 24.10.26
# ruff: noqa: E402

"""login.toml ``process`` (live_mux / live_decrypt), config.toml keys and CLI flags decide the streaming mux and in-flight decryption."""

import sys
from pathlib import Path

import pytest

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.downloader.util import live_switches
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import site_extra_args
from VibraVid.services._base.site_extra_args import get_site_login_status, resolve_site_process


class FakeLogin:
    def __init__(self, sections):
        self.sections = sections

    def get_section(self, name):
        return dict(self.sections.get(name, {}))

    def has_section(self, name):
        return name in self.sections

    def get(self, section, key, default=None):
        return self.sections.get(section, {}).get(key, default)


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get_bool(self, section, key, default=None):
        return self.values.get((section, key), default)


@pytest.fixture
def env(monkeypatch):
    """``env.login(...)`` / ``env.config(...)`` set the fake files; CLI flags and site name are reset around every test."""
    login_cm = type("CM", (), {"login": FakeLogin({})})()
    config_cm = type("CM", (), {"config": FakeConfig({})})()
    monkeypatch.setattr(site_extra_args, "config_manager", login_cm)
    monkeypatch.setattr(live_switches, "config_manager", config_cm)

    class Env:
        @staticmethod
        def login(**sections):
            login_cm.login = FakeLogin(sections)

        @staticmethod
        def config(**values):
            config_cm.config = FakeConfig({tuple(k.split("__")): v for k, v in values.items()})

    saved = (context_tracker.no_livemux, context_tracker.force_livemux, context_tracker.no_live_decrypt, context_tracker.site_name)
    context_tracker.no_livemux = context_tracker.force_livemux = context_tracker.no_live_decrypt = False
    context_tracker.site_name = None
    yield Env
    context_tracker.no_livemux, context_tracker.force_livemux, context_tracker.no_live_decrypt, context_tracker.site_name = saved


def test_process_is_read_from_a_table_a_json_string_and_unset(env):
    env.login(a={"process": {"live_mux": False, "live_decrypt": True}}, b={"process": '{"live_mux": false}'}, c={"cdm": ""})

    assert resolve_site_process("a") == {"live_mux": False, "live_decrypt": True}
    assert resolve_site_process("b") == {"live_mux": False, "live_decrypt": None}
    assert resolve_site_process("c") == {"live_mux": None, "live_decrypt": None}
    assert resolve_site_process("missing") == {"live_mux": None, "live_decrypt": None}
    assert resolve_site_process("") == {"live_mux": None, "live_decrypt": None}


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", 3, {"live_mux": "maybe"}, {"unknown": True}])
def test_a_broken_process_never_raises_and_is_ignored(env, raw):
    env.login(a={"process": raw})

    assert resolve_site_process("a") == {"live_mux": None, "live_decrypt": None}


def test_string_switches_are_accepted(env):
    env.login(a={"process": {"live_mux": "false", "live_decrypt": "true"}})

    assert resolve_site_process("a") == {"live_mux": False, "live_decrypt": True}


def test_process_is_not_a_credential(env):
    env.login(a={"process": {"live_mux": False}})

    assert get_site_login_status("a") is None


def test_without_a_process_line_everything_is_off(env):
    env.login(a={"cdm": ""})

    assert live_switches.live_decrypt_allowed("a") is False
    assert live_switches.live_mux_allowed("a")[0] is False
    assert live_switches.live_mux_allowed("unknown_site")[0] is False
    assert live_switches.live_decrypt_allowed(None) is False


def test_login_process_switches_the_site_on(env):
    env.login(a={"process": {"live_mux": True, "live_decrypt": True}}, b={})

    assert live_switches.live_mux_allowed("a", encrypted_tracks=True) == (True, "")
    assert live_switches.live_decrypt_allowed("a") is True
    assert live_switches.live_mux_allowed("b")[0] is False


def test_config_switch_is_the_fallback_and_login_overrides_it(env):
    env.login(a={}, b={"process": {"live_mux": False}}, c={"process": {"live_decrypt": True}})
    env.config(PROCESS__use_live_mux=True, DOWNLOAD__use_live_decrypt=True)

    assert live_switches.live_mux_allowed("a") == (True, "")
    assert live_switches.live_mux_allowed("b")[0] is False
    assert live_switches.live_mux_allowed("c") == (True, "")  # live_mux unset in its process -> config.toml
    assert live_switches.live_decrypt_allowed("a") is True


def test_no_live_decrypt_flag_beats_login_and_config(env):
    env.login(a={"process": {"live_decrypt": True}})
    env.config(DOWNLOAD__use_live_decrypt=True)
    assert live_switches.live_decrypt_allowed("a") is True

    context_tracker.no_live_decrypt = True
    assert live_switches.live_decrypt_allowed("a") is False


def test_live_decrypt_off_keeps_the_streaming_mux_for_clear_tracks_only(env):
    env.login(a={"process": {"live_mux": True, "live_decrypt": False}}, b={"process": {"live_mux": True, "live_decrypt": True}})

    assert live_switches.live_mux_allowed("a", encrypted_tracks=False) == (True, "")
    allowed, why = live_switches.live_mux_allowed("a", encrypted_tracks=True)
    assert allowed is False and "live decrypt" in why
    assert live_switches.live_mux_allowed("b", encrypted_tracks=True) == (True, "")


def test_live_mux_off_and_live_decrypt_on_is_independent(env):
    env.login(a={"process": {"live_mux": False, "live_decrypt": True}})

    assert live_switches.live_mux_allowed("a", encrypted_tracks=True)[0] is False
    assert live_switches.live_decrypt_allowed("a") is True


def test_cli_flags_win_over_login_and_config(env):
    env.login(a={"process": {"live_mux": False}})

    context_tracker.force_livemux = True
    assert live_switches.live_mux_allowed("a") == (True, "")

    context_tracker.no_livemux = True
    assert live_switches.live_mux_allowed("a")[0] is False


def test_force_livemux_never_muxes_ciphertext(env):
    env.login(a={"process": {"live_decrypt": False}})
    context_tracker.force_livemux = True

    assert live_switches.live_mux_allowed("a", encrypted_tracks=True)[0] is False
    assert live_switches.live_mux_allowed("a", encrypted_tracks=False)[0] is True


def test_site_defaults_to_the_one_in_the_tracker(env):
    env.login(a={"process": {"live_decrypt": False}})
    context_tracker.site_name = "a"

    assert live_switches.live_decrypt_allowed() is False
