# 19.10.26

"""A service calling ``sys.exit()`` during a TUI search must become an error for that site, never the end of the app."""

import sys

import pytest

from VibraVid.tui import bridge


def test_a_service_exit_becomes_a_service_exit_error_with_what_it_printed():
    bridge.install_stdio_proxy()
    try:
        with pytest.raises(bridge.ServiceExit, match="login failed") as info:
            with bridge.capture_stdio():
                print("login failed: check login.toml")
                raise SystemExit(1)
        assert isinstance(info.value, RuntimeError)  # the workers' `except Exception` now catches it
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__


def test_a_silent_exit_still_says_something():
    with pytest.raises(bridge.ServiceExit, match="exit code 3"):
        with bridge.capture_stdio():
            raise SystemExit(3)


def test_one_site_exiting_does_not_hide_the_others(monkeypatch):
    def fake_search(site, query):
        if site == "bad":
            raise SystemExit(1)
        return ["hit"]

    monkeypatch.setattr(bridge, "search_titles", lambda site, query: _run(fake_search, site, query))
    results, errors = bridge.search_global("q", sites=["good", "bad"])

    assert results == {"good": ["hit"]}
    assert "bad" in errors and "exit code 1" in errors["bad"]


def _run(fn, site, query):
    with bridge.capture_stdio():
        return fn(site, query)


def test_a_failed_download_start_raises_the_services_own_reason(monkeypatch):
    class FailingApi:
        def start_download(self, item, season=None, episodes=None):  # noqa: ARG002
            print("Error getting stream: Rakuten TV email/password not set")
            return False

    monkeypatch.setattr(bridge, "_get_api", lambda site: FailingApi())

    bridge.install_stdio_proxy()
    try:
        with pytest.raises(RuntimeError, match="email/password not set"):
            bridge.start_download("rakutentv", object())
    finally:
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
