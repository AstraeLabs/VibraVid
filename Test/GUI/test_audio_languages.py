"""Language controls, validated requests and batch planning, without provider downloads."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django
django.setup()

from django.test import RequestFactory

from searchapp.api.base import Episode, Season
from searchapp.forms import DownloadForm
from searchapp.views import _shared
from searchapp.views import download as download_module
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.utils import config_manager


def _config(raw="ita|eng|fra"):
    original = config_manager.config.get
    return patch.object(config_manager.config, "get", side_effect=lambda section, key, *args, **kwargs:
                        raw if (section, key) == ("DOWNLOAD", "select_audio") else original(section, key, *args, **kwargs))


def fixture_seasons():
    return [Season(22, [Episode(1, "Italian only", language="it"), Episode(2, "English only", language="en"),
                        Episode(3, "Both languages", language="en,it")]),
            Season(23, [Episode(1, "Collide", language="en"), Episode(2, "Slippin' and Slidin", language="en")])]


def test_form_accepts_only_configured_choices():
    with _config("ita|eng|fra"):
        assert DownloadForm({"source_alias": "streamingcommunity", "audio_language": "fr"}).is_valid()
        assert DownloadForm({"source_alias": "streamingcommunity", "audio_language": "all"}).is_valid()
        assert not DownloadForm({"source_alias": "streamingcommunity", "audio_language": "ja"}).is_valid()


def test_full_series_batch_excludes_seasons_without_requested_language(monkeypatch):
    api = SimpleNamespace(get_series_metadata=lambda item: fixture_seasons())
    calls = []
    monkeypatch.setattr(_shared, "get_api", lambda site: api)
    monkeypatch.setattr(_shared, "_run_download_in_thread", lambda *args, **kwargs: calls.append(kwargs) or (lambda: None))
    monkeypatch.setattr(_shared, "_submit_download_task", lambda worker: None)
    request = RequestFactory().post("/series-detail/", {
        "source_alias": "streamingcommunity", "item_payload": json.dumps({"name": "Grey's Anatomy", "type": "tv"}),
        "download_type": "full_series", "audio_language": "it",
    })
    request._messages = Mock()
    with _config():
        response = _shared._handle_series_download(request)
    assert response.status_code == 302
    assert [(call["season"], call["audio_language"]) for call in calls] == [("22", "it")]
    assert [ep.number for ep in calls[0]["_metadata"][0].episodes] == [1, 3]
    assert context_tracker.audio_filter is None


def test_movie_download_receives_selected_audio(monkeypatch):
    calls = []
    monkeypatch.setattr(download_module, "_run_download_in_thread", lambda *args, **kwargs: calls.append(kwargs))
    request = RequestFactory().post("/download/", {
        "source_alias": "streamingcommunity", "item_payload": json.dumps({"name": "Movie", "type": "movie"}),
        "audio_language": "en",
    })
    with _config():
        response = download_module.start_download(request)
    assert response.status_code == 302
    assert calls[0]["audio_language"] == "en"


def test_audio_inspection_is_scoped_and_reports_actual_languages(monkeypatch):
    observed = []
    api = SimpleNamespace(
        get_available_qualities=lambda *args: observed.append(context_tracker.audio_filter) or ["1080p"],
        get_available_audio_languages=lambda *args: [{"code": "en", "name": "English"}],
    )
    monkeypatch.setattr(download_module, "get_api", lambda site: api)
    request = RequestFactory().post("/api/available-qualities/", json.dumps({
        "source_alias": "streamingcommunity", "item_payload": {"name": "Movie", "type": "movie"},
        "audio_language": "en",
    }), content_type="application/json")
    with _config():
        response = download_module.available_qualities(request)
    assert response.status_code == 200
    assert json.loads(response.content)["audio_languages"] == [{"code": "en", "name": "English"}]
    assert observed == ["en"]
    assert context_tracker.audio_filter is None


def test_exact_episode_inspection_does_not_reload_every_season(monkeypatch):
    api = SimpleNamespace(
        get_series_metadata=Mock(side_effect=AssertionError("Unnecessary full-series reload")),
        get_available_qualities=Mock(return_value=["720p"]),
        get_available_audio_languages=Mock(return_value=[{"code": "en", "name": "English"}]),
    )
    monkeypatch.setattr(download_module, "get_api", lambda site: api)
    request = RequestFactory().post("/api/available-qualities/", json.dumps({
        "source_alias": "streamingcommunity", "item_payload": {"name": "Grey's Anatomy", "type": "tv"},
        "season": 23, "episode": 1, "audio_language": "en",
    }), content_type="application/json")
    with _config():
        response = download_module.available_qualities(request)
    assert response.status_code == 200
    api.get_series_metadata.assert_not_called()
    assert api.get_available_audio_languages.call_args.args[1:] == (23, 1)


def test_audio_inspection_does_not_expose_validation_exception(monkeypatch):
    monkeypatch.setattr(download_module, "get_api", lambda site: Mock())
    monkeypatch.setattr(download_module, "normalize_audio_language",
                        Mock(side_effect=ValueError("private configuration path: /secret/audio.toml")))
    request = RequestFactory().post("/api/available-qualities/", json.dumps({
        "source_alias": "streamingcommunity", "item_payload": {"name": "Movie", "type": "movie"},
        "audio_language": "invalid",
    }), content_type="application/json")

    response = download_module.available_qualities(request)

    assert response.status_code == 400
    assert json.loads(response.content) == {
        "qualities": [], "message": "Choose an audio language from the configured languages."
    }


def test_audio_inspection_does_not_expose_provider_exception(monkeypatch):
    api = SimpleNamespace(get_available_qualities=Mock(
        side_effect=NotImplementedError("private provider token: secret-value")))
    monkeypatch.setattr(download_module, "get_api", lambda site: api)
    request = RequestFactory().post("/api/available-qualities/", json.dumps({
        "source_alias": "streamingcommunity", "item_payload": {"name": "Movie", "type": "movie"},
    }), content_type="application/json")

    response = download_module.available_qualities(request)

    assert response.status_code == 200
    assert json.loads(response.content) == {
        "qualities": [], "supported": False, "audio_supported": False,
        "message": "This provider does not support quality inspection."
    }


def test_series_metadata_does_not_expose_provider_exception(monkeypatch):
    monkeypatch.setattr(download_module, "get_api", Mock(
        side_effect=RuntimeError("private provider token: secret-value")))
    request = RequestFactory().post("/series-metadata/", json.dumps({
        "source_alias": "streamingcommunity", "item_payload": {"name": "Movie", "type": "movie"},
    }), content_type="application/json")

    response = download_module.series_metadata(request)

    assert response.status_code == 500
    assert json.loads(response.content) == {
        "Error get metadata": "Unable to get series metadata right now."
    }


def test_language_filter_is_on_the_results_page_and_not_on_the_home_page():
    """Home only starts a search (it still carries the choice in a hidden field); the filter lives where results are."""
    from django.template.loader import render_to_string
    from django.test import Client

    with _config("ita|eng"):
        home = Client().get("/", HTTP_HOST="127.0.0.1").content.decode("utf-8")
        results = render_to_string("searchapp/cinema_results.html", {"results": [{"title": "Doctor Who"}], "query": "doctor who"}, request=RequestFactory().get("/search/", {"query": "doctor who"}))

    assert "data-language-filter" not in home and 'name="audio_language"' in home
    assert "data-language-filter" in results
