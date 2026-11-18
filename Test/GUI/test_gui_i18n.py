"""GUI translation: language resolution, the shipped catalogs and what the pages carry (#790)."""

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

from django.core.management import call_command
from django.test import Client, RequestFactory

from searchapp import i18n
from VibraVid.utils import config_manager

PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.fixture(autouse=True)
def _db():
    call_command("migrate", verbosity=0, interactive=False)


@pytest.fixture
def configured(monkeypatch):
    def set_language(value):
        original = config_manager.config.get
        monkeypatch.setattr(
            config_manager.config, "get",
            lambda section, key, *args, **kwargs: value if (section, key) == ("DEFAULT", "gui_language") else original(section, key, *args, **kwargs),
        )
    return set_language


@pytest.mark.parametrize("code", [c for c in i18n.available_languages() if c != "en"])
def test_catalog_is_valid_and_keeps_placeholders(code):
    catalog = i18n.load_catalog(code)
    assert catalog
    for source, translated in catalog.items():
        assert isinstance(translated, str) and translated.strip(), source
        assert set(PLACEHOLDER.findall(source)) == set(PLACEHOLDER.findall(translated)), source


def test_italian_is_shipped_and_english_needs_no_catalog():
    assert "it" in i18n.available_languages()
    assert i18n.load_catalog("en") == {} and i18n.load_catalog("xx") == {}
    assert i18n.load_catalog("it")["Save"] == "Salva"


def test_language_resolution_order(configured):
    factory = RequestFactory()
    configured("auto")
    assert i18n.resolve_language(factory.get("/", HTTP_ACCEPT_LANGUAGE="it-IT,it;q=0.9,en;q=0.5")) == "it"
    assert i18n.resolve_language(factory.get("/", HTTP_ACCEPT_LANGUAGE="fr-FR,fr;q=0.9")) == "en"
    assert i18n.resolve_language(factory.get("/")) == "en"

    configured("it")
    assert i18n.resolve_language(factory.get("/", HTTP_ACCEPT_LANGUAGE="en-US")) == "it"

    request = factory.get("/", HTTP_ACCEPT_LANGUAGE="it")
    request.COOKIES[i18n.COOKIE_NAME] = "en"  # the header selector beats the config and the browser
    assert i18n.resolve_language(request) == "en"
    request.COOKIES[i18n.COOKIE_NAME] = "klingon"
    assert i18n.resolve_language(request) == "it"


def test_pages_carry_the_catalog_only_for_a_translated_language(configured):
    configured("auto")
    italian = Client().get("/logs/", HTTP_HOST="127.0.0.1", HTTP_ACCEPT_LANGUAGE="it").content.decode("utf-8")
    english = Client().get("/logs/", HTTP_HOST="127.0.0.1", HTTP_ACCEPT_LANGUAGE="en").content.decode("utf-8")

    assert '<html lang="it">' in italian and 'id="cn-i18n"' in italian
    assert '<html lang="en">' in english and 'id="cn-i18n"' not in english
    assert "searchapp/i18n.js" in english  # window.t exists either way
    assert 'id="cn-lang"' in english  # the selector is always there to switch


def test_provider_content_is_never_translated_by_markup():
    script = (ROOT / "GUI" / "searchapp" / "static" / "searchapp" / "i18n.js").read_text(encoding="utf-8")
    for selector in ("data-no-tr", ".cn-ptitle", ".cn-cap", ".cn-t"):
        assert selector in script
