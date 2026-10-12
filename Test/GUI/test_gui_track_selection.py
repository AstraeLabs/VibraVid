# ruff: noqa: E402

"""Regression coverage for GUI per-download CLI track selection."""

import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for directory in (ROOT, GUI_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest

from searchapp.forms import DownloadForm, TrackSelectionForm
from searchapp.views import _shared
from VibraVid.core.ui.tracker import ContextTracker, context_tracker
from VibraVid.core.velora import base as velora_base


@pytest.fixture(autouse=True)
def restore_context():
    saved_global = ContextTracker._global_is_gui
    saved_local = dict(context_tracker.local.__dict__)
    yield
    ContextTracker._global_is_gui = saved_global
    context_tracker.local.__dict__.clear()
    context_tracker.local.__dict__.update(saved_local)


def test_track_selection_form_accepts_cli_filters():
    form = DownloadForm({
        "source_alias": "streamingcommunity",
        "item_payload": '{"name":"Example","type":"film"}',
        "video_filter": "r=2160:c=hvc1:f=best",
        "audio_filter": "1ita|2eng",
        "subtitle_filter": "false",
        "skip_no_match": "on",
    })
    assert form.is_valid(), form.errors
    assert form.cleaned_data["video_filter"] == "r=2160:c=hvc1:f=best"
    assert form.cleaned_data["audio_filter"] == "1ita|2eng"
    assert form.cleaned_data["subtitle_filter"] == "false"
    assert form.cleaned_data["skip_no_match"] is True


def test_track_selection_form_defaults_to_config_and_rejects_newlines():
    form = TrackSelectionForm({})
    assert form.is_valid(), form.errors
    assert form.cleaned_data == {
        "video_filter": "",
        "audio_filter": "",
        "subtitle_filter": "",
        "skip_no_match": False,
    }
    malformed = TrackSelectionForm({"audio_filter": "ita\n--unsafe"})
    assert not malformed.is_valid()
    assert "audio_filter" in malformed.errors


def test_velora_selector_prefers_gui_filters_over_provider_and_config(monkeypatch):
    captured = []

    class RecordingSelector:
        no_match = False

        def __init__(self, video, audio, subtitle, **kwargs):
            captured.append((video, audio, subtitle, kwargs["strict_no_match"]))

        def apply(self, streams):
            return ("video", "audio", "subtitle")

    monkeypatch.setattr(velora_base, "StreamSelector", RecordingSelector)
    instance = object.__new__(velora_base.BaseMediaDownloader)
    instance.custom_filters = {"video": "720", "audio": "eng", "subtitle": "eng"}
    instance.streams = []
    context_tracker.video_quality = None
    context_tracker.track_filters = {"video": "2160", "audio": "ita", "subtitle": "false"}
    context_tracker.skip_no_match = True

    instance._apply_selection()

    assert captured == [("2160", "ita", "false", True)]
    assert instance._effective_filter("audio") == "ita"
    assert instance._effective_filter("subtitle") == "false"
    assert (instance._sv, instance._sa, instance._ss) == ("video", "audio", "subtitle")


def test_gui_track_filters_are_thread_local():
    context_tracker.track_filters = {"audio": "ita"}
    observed = []

    def run():
        assert context_tracker.track_filters == {}
        context_tracker.track_filters = {"audio": "eng"}
        observed.append(context_tracker.track_filters)

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=3)

    assert not t.is_alive()
    assert observed == [{"audio": "eng"}]
    assert context_tracker.track_filters == {"audio": "ita"}


def test_download_worker_sets_and_clears_filters(monkeypatch):
    observed = []

    class Provider:
        def start_download(self, media_item, season=None, episodes=None):
            observed.append((
                dict(context_tracker.track_filters),
                context_tracker.skip_no_match,
                context_tracker.video_quality,
            ))
            return False

    monkeypatch.setattr(_shared, "get_api", lambda site: Provider())
    monkeypatch.setattr(_shared, "_add_scheduled_download", lambda *a, **k: None)
    monkeypatch.setattr(_shared, "_remove_scheduled_download", lambda *a, **k: None)
    monkeypatch.setattr(_shared, "_acquire_download_slot", lambda: None)
    monkeypatch.setattr(_shared, "_release_download_slot", lambda: None)
    monkeypatch.setattr(_shared, "_is_scheduled_cancelled", lambda download_id: False)
    monkeypatch.setattr(_shared, "_mark_task_failed", lambda *a, **k: None)
    monkeypatch.setattr(_shared, "_log_gui_equivalent_command", lambda *a, **k: None)

    task = _shared._run_download_in_thread(
        "streamingcommunity",
        {"name": "Example", "type": "film", "slug": ""},
        quality="1080p",
        video_filter="2160",
        audio_filter="ita",
        subtitle_filter="false",
        skip_no_match=True,
        _submit=False,
    )
    with pytest.raises(RuntimeError, match="reported that the download did not complete successfully"):
        task()

    assert observed == [({"video": "2160", "audio": "ita", "subtitle": "false"}, True, None)]
    assert context_tracker.track_filters == {}
    assert context_tracker.skip_no_match is False
    assert context_tracker.video_quality is None


def test_equivalent_command_reports_gui_track_selections(monkeypatch):
    captured = []
    monkeypatch.setattr(
        _shared.equivalent_command_builder, "log_equivalent_command_from_params",
        lambda **kwargs: captured.append(kwargs),
    )
    _shared._log_gui_equivalent_command(
        "streamingcommunity", {"name": "Example"},
        options={"video": "2160", "audio": "ita", "subtitle": "false", "skip_no_match": True},
    )
    assert captured[0]["options"] == {
        "video": "2160", "audio": "ita", "subtitle": "false", "skip_no_match": True,
    }
