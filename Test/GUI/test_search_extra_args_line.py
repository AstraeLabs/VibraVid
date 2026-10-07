# 06.10.26
# ruff: noqa: E402

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for path in (ROOT, GUI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from django.core.management import call_command
from django.test import Client

from searchapp.views import cinema


@pytest.fixture(scope="module")
def client():
    call_command("migrate", verbosity=0)
    return Client(HTTP_HOST="localhost")


def _login(monkeypatch, content: str):
    real = cinema._conf_text
    monkeypatch.setattr(cinema, "_conf_text", lambda name: content if name == "login.json" else real(name))


def _embedded_map(page: str) -> dict:
    match = re.search(r'<script id="cn-site-extra-args" type="application/json">(.*?)</script>', page, re.S)
    assert match, "the search page must embed the per-provider custom arguments"
    return json.loads(match.group(1))


def test_map_only_has_providers_with_custom_arguments(monkeypatch):
    _login(monkeypatch, json.dumps({
        "a": {"extra_args": "--url x"},
        "b": {"extra_args": ""},
        "c": {"token": "t"},
        "d": "not a block",
    }))

    assert cinema._site_extra_args_map() == {"a": "--url x"}


@pytest.mark.parametrize("content", ["not json", "[]", "5", "# Non riesco a leggere login.json: boom"])
def test_unreadable_or_odd_login_json_gives_an_empty_map(monkeypatch, content):
    _login(monkeypatch, content)

    assert cinema._site_extra_args_map() == {}


def test_search_page_embeds_the_map_and_the_info_line(client, monkeypatch):
    _login(monkeypatch, json.dumps({"streamingcommunity": {"extra_args": "--url <b>x</b> & y"}}))

    page = client.get("/").content.decode()

    assert _embedded_map(page) == {"streamingcommunity": "--url <b>x</b> & y"}
    assert 'id="cn-extra-line"' in page
    assert "<b>x</b>" not in page  # json_script escapes it, so the value can't inject markup


def test_search_page_without_custom_arguments(client, monkeypatch):
    _login(monkeypatch, "{}")

    assert _embedded_map(client.get("/").content.decode()) == {}


def test_settings_page_still_gets_the_same_map(client, monkeypatch):
    _login(monkeypatch, json.dumps({"raiplay": {"extra_args": "--foo"}}))

    page = client.get("/settings/").content.decode()
    match = re.search(r'<script id="cn-src-extra-args" type="application/json">(.*?)</script>', page, re.S)

    assert match and json.loads(match.group(1)) == {"raiplay": "--foo"}


def test_search_page_reads_the_options_from_the_cli_schema_endpoint(client, monkeypatch):
    _login(monkeypatch, "{}")

    page = client.get("/").content.decode()

    assert 'var URL_CLI_OPTIONS = "/api/site-cli-options/"' in page


def test_the_schema_endpoint_lists_mediaset_infinity_options(client):
    data = client.get("/api/site-cli-options/", {"site": "mediasetinfinity"}).json()
    by_flag = {arg["flag"]: arg for arg in data["args"]}

    assert {"--url", "--country", "--disable"} <= set(by_flag)
    assert by_flag["--country"]["kind"] == "choice" and by_flag["--country"]["choices"] == ["it", "es"]
    assert by_flag["--disable"]["kind"] == "flag"
    assert by_flag["--url"]["kind"] == "text"
