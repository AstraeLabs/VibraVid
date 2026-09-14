# 14.09.26
# ruff: noqa: E402

import sys
from pathlib import Path


workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))


from VibraVid.services.crunchyroll.downloader import parse_select_audio_filter
from VibraVid.services.streamingcommunity import _effective_languages
from VibraVid.utils import config_manager


def test_parse_select_audio_filter_legacy_single_group():
    # No numeric prefix -- one flat group, same as before the slot feature.
    assert parse_select_audio_filter("ita|eng") == [["it-IT", "en-US"]]


def test_parse_select_audio_filter_numbered_slots():
    assert parse_select_audio_filter("1ita|2eng") == [["it-IT"], ["en-US"]]


def test_parse_select_audio_filter_multi_lang_same_slot():
    # Two languages in the same slot -- still one group, both kept together.
    assert parse_select_audio_filter("1ita|1fra") == [["it-IT", "fr-FR"]]


def test_parse_select_audio_filter_empty_and_all():
    assert parse_select_audio_filter("") == []
    assert parse_select_audio_filter("all") == []
    assert parse_select_audio_filter("false") == []


def test_parse_select_audio_filter_unrecognised_code_dropped():
    # "xx" isn't a recognised language code -- must be dropped, not leak in as "".
    assert parse_select_audio_filter("1xx|2eng") == [["en-US"]]


def _set_select_audio(monkeypatch, value):
    """Redirect config_manager.config.get("DOWNLOAD", "select_audio", ...) to a fixed value."""
    real_get = config_manager.config.get

    def fake_get(section, key, *args, **kwargs):
        if section == "DOWNLOAD" and key == "select_audio":
            return value
        return real_get(section, key, *args, **kwargs)

    monkeypatch.setattr(config_manager.config, "get", fake_get)


def test_effective_languages_slot_fallthrough(monkeypatch):
    # Slot 1 (fra) isn't a supported catalog language -- must fall through to slot 2 (eng).
    _set_select_audio(monkeypatch, "1fra|2eng")
    assert _effective_languages() == ["en"]


def test_effective_languages_legacy_unchanged(monkeypatch):
    _set_select_audio(monkeypatch, "ita|eng")
    assert _effective_languages() == ["it", "en"]


def test_effective_languages_no_slot_matches_falls_back_to_default(monkeypatch):
    # Neither fra nor deu map to a supported catalog language -- default to both.
    _set_select_audio(monkeypatch, "1fra|2deu")
    assert _effective_languages() == ["it", "en"]


def test_effective_languages_empty_config_defaults(monkeypatch):
    _set_select_audio(monkeypatch, "")
    assert _effective_languages() == ["it", "en"]
