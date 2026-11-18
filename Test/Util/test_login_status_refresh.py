# 12.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest
from rich.console import Console

from VibraVid.services._base import login_status
from VibraVid.services._base.login_status import Login


@pytest.fixture
def out(monkeypatch):
    console = Console(record=True, width=120, force_terminal=False)
    monkeypatch.setattr(login_status, "console", console)
    Login._cached_seen.clear()
    Login._open = False
    Login._banner_shown = False
    return console


def test_refresh_messages(out):
    Login.refresh_try()
    Login.refresh_failed()
    Login.refresh_failed("rejected 400")
    Login.refresh_ok()
    Login.refresh_ok(1807000000)

    lines = out.export_text().splitlines()
    assert lines[:5] == ["", "[+] Try refresh token...", "[-] Failed", "[-] Failed (rejected 400)", "[+] Success"]
    assert lines[5].startswith("[+] Success (valid until ") and lines[5].endswith("/2027)")


def test_crunchyroll_refresh_reports_and_reraises(out, monkeypatch):
    from VibraVid.services.crunchyroll.client import CrunchyrollClient

    client = object.__new__(CrunchyrollClient)
    client.refresh_token = "x"

    monkeypatch.setattr(CrunchyrollClient, "_refresh_request", lambda self: None)
    client._refresh()

    def boom(self):
        raise RuntimeError("Token refresh failed: 400")

    monkeypatch.setattr(CrunchyrollClient, "_refresh_request", boom)
    with pytest.raises(RuntimeError):
        client._refresh()

    assert out.export_text().splitlines() == [
        "",
        "[+] Try refresh token...",
        "[+] Success",
        "[+] Try refresh token...",
        "[-] Failed (Token refresh failed: 400)",
    ]


def test_crunchyroll_refresh_without_token_prints_nothing(out):
    from VibraVid.services.crunchyroll.client import CrunchyrollClient

    client = object.__new__(CrunchyrollClient)
    client.refresh_token = None
    with pytest.raises(RuntimeError, match="refresh_token missing"):
        client._refresh()
    assert out.export_text() == ""


def test_login_show_banner_lines(out):
    Login.show(Login.ACCOUNT, user="me", details={"Plans": "A, B", "Empty": ""})
    Login.show(Login.ANONYMOUS)

    assert out.export_text().splitlines() == [
        "",
        "[+] Login type: Account",
        "[+] User: me",
        "[+] Plans: A, B",
        "",
        "",
        "[+] Login type: Anonymous",
        "",
    ]


def test_refresh_cached_prints_path_once_per_file(out):
    Login.refresh_cached(r"C:\cache\a [x].json")
    Login.refresh_cached(r"C:\cache\a [x].json")
    Login.refresh_cached(r"C:\cache\b.json")

    assert out.export_text().splitlines() == [
        "",
        "[+] Try refresh token...",
        r"[/] Use cached token (C:\cache\a [x].json)",
        "[+] Try refresh token...",
        r"[/] Use cached token (C:\cache\b.json)",
    ]


def test_token_saved_by_refresh_is_not_announced_as_cached(out):
    Login.refresh_try()
    Login.refresh_ok(1807000000, r"C:\cache\a.json")
    Login.refresh_cached(r"C:\cache\a.json")  # a later call of the same run reuses the file just written
    Login.refresh_cached(r"C:\cache\b.json")

    lines = out.export_text().splitlines()
    assert lines[:2] == ["", "[+] Try refresh token..."]
    assert lines[2].startswith("[+] Success (valid until ")
    assert lines[3:] == ["[+] Try refresh token...", r"[/] Use cached token (C:\cache\b.json)"]


def test_startup_block_is_wrapped_in_blank_lines(out):
    Login.refresh_try()
    Login.refresh_ok()
    Login.show(Login.ACCOUNT, user="me")

    assert out.export_text().splitlines() == [
        "",
        "[+] Try refresh token...",
        "[+] Success",
        "[+] Login type: Account",
        "[+] User: me",
        "",
    ]


def test_refresh_after_the_banner_is_its_own_block(out):
    Login.show(Login.ACCOUNT)
    Login.refresh_try()
    Login.refresh_failed("rejected 400")

    assert out.export_text().splitlines() == [
        "",
        "[+] Login type: Account",
        "",
        "",
        "[+] Try refresh token...",
        "[-] Failed (rejected 400)",
        "",
    ]
