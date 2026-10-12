# ruff: noqa: E402
"""GUI manual selection regression tests. No provider network calls."""

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
GUI = ROOT / "GUI"
for item in (ROOT, GUI):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from django.urls import reverse

from searchapp.forms import DownloadForm
from VibraVid.core.ui.manual_tracks import ManualTrackBroker, TrackSelectionError
from VibraVid.core.ui.tracker import context_tracker


def test_gui_form_accepts_manual_tracks():
    data = {"source_alias": "streamingcommunity", "manual_tracks": "on"}
    form = DownloadForm(data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["manual_tracks"] is True
    assert DownloadForm({"source_alias": "streamingcommunity"}).is_valid()


def test_parallel_manual_selection_is_isolated_and_does_not_expose_urls():
    broker = ManualTrackBroker()
    results = {}
    source = {
        key: [SimpleNamespace(type="video", selected=True, url="https://x.test?token=SECRET"),
              SimpleNamespace(type="audio", selected=False, language="ita")]
        for key in ("A", "B")
    }

    def runner(key):
        results[key] = broker.choose(key, source[key], timeout=4)

    threads = [threading.Thread(target=runner, args=(key,)) for key in source]
    for thread in threads:
        thread.start()
    for _ in range(300):
        if len(broker.list_pending()) == 2:
            break
        time.sleep(.01)
    pending = {item["download_id"]: item for item in broker.list_pending()}
    assert set(pending) == {"A", "B"}
    assert "SECRET" not in str(pending)
    with pytest.raises(TrackSelectionError):
        broker.submit("A", "wrong-token", [0])
    broker.submit("A", pending["A"]["token"], [0])
    broker.submit("B", pending["B"]["token"], [1])
    for thread in threads:
        thread.join(timeout=4)
        assert not thread.is_alive()
    assert results == {"A": {0}, "B": {1}}
    assert [s.selected for s in source["A"]] == [True, False]
    assert [s.selected for s in source["B"]] == [False, True]
    assert broker.list_pending() == []


def test_invalid_selection_rejected():
    broker = ManualTrackBroker()
    tracks = [SimpleNamespace(type="video", selected=True)]
    cancelled = threading.Event()
    errors = []

    def runner():
        try:
            broker.choose("A", tracks, cancelled=cancelled.is_set, timeout=3)
        except TrackSelectionError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=runner)
    thread.start()
    for _ in range(200):
        pending = broker.list_pending()
        if pending:
            break
        time.sleep(.01)
    assert pending
    token = pending[0]["token"]
    for chosen in ([], [True], [-1], [1], [0, 0], "0"):
        with pytest.raises(TrackSelectionError):
            broker.submit("A", token, chosen)
    cancelled.set()
    thread.join(timeout=4)
    assert not thread.is_alive()
    assert errors == ["Track selection cancelled"]
    assert broker.list_pending() == []


def test_endpoint_is_post_only():
    from django.test import Client

    response = Client().get(reverse("confirm_manual_tracks"))
    assert response.status_code == 405


def test_context_flag_is_thread_local():
    context_tracker.manual_tracks = True
    observed = []

    def run():
        observed.append(context_tracker.manual_tracks)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=3)
    try:
        assert observed == [False]
    finally:
        context_tracker.manual_tracks = False


def test_reuses_same_catalogue_across_episodes_and_forgets_after_job():
    broker = ManualTrackBroker()
    original = [
        SimpleNamespace(type="video", selected=True, height=1080, width=1920,
                        codecs="avc1", bitrate=4200000),
        SimpleNamespace(type="audio", selected=True, language="ita", codecs="aac", bitrate=128000),
    ]
    later = [
        SimpleNamespace(type="video", selected=False, height=1080, width=1920,
                        codecs="avc1", bitrate=4400000),
        SimpleNamespace(type="audio", selected=False, language="ita", codecs="aac", bitrate=132000),
    ]
    selected = []
    thread = threading.Thread(target=lambda: selected.append(broker.choose("series-job", original, timeout=3)))
    thread.start()
    for _ in range(200):
        pending = broker.list_pending()
        if pending:
            break
        time.sleep(.01)
    assert pending
    broker.submit("series-job", pending[0]["token"], [0, 1])
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert selected == [{0, 1}]
    assert broker.choose("series-job", later, timeout=0.01) == {0, 1}
    assert [track.selected for track in later] == [True, True]
    assert broker.list_pending() == []
    broker.forget("series-job")
    with pytest.raises(TrackSelectionError, match="timed out"):
        broker.choose("series-job", later, timeout=0.01)


def test_catalogue_change_requires_another_manual_selection():
    broker = ManualTrackBroker()
    first = [SimpleNamespace(type="video", selected=True, height=1080, codecs="avc1")]
    changed = [SimpleNamespace(type="video", selected=True, height=720, codecs="avc1")]
    worker = threading.Thread(target=lambda: broker.choose("job", first, timeout=3))
    worker.start()
    for _ in range(200):
        pending = broker.list_pending()
        if pending:
            break
        time.sleep(.01)
    assert pending
    broker.submit("job", pending[0]["token"], [0])
    worker.join(timeout=3)
    assert not worker.is_alive()
    with pytest.raises(TrackSelectionError, match="timed out"):
        broker.choose("job", changed, timeout=0.01)
    broker.forget("job")


def test_manual_broker_releases_slot_before_wait_and_reacquires_on_accept():
    broker = ManualTrackBroker()
    events = []
    tracks = [SimpleNamespace(type="video", selected=True)]
    results = []

    def worker():
        results.append(broker.choose(
            "worker", tracks, timeout=3,
            on_wait=lambda: events.append("released"),
            on_confirm=lambda: events.append("reacquired"),
        ))

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(200):
        pending = broker.list_pending()
        if pending:
            break
        time.sleep(.01)
    assert pending
    for _ in range(200):
        if events:
            break
        time.sleep(.01)
    assert events == ["released"]
    broker.submit("worker", pending[0]["token"], [0])
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert results == [{0}]
    assert events == ["released", "reacquired"]


def test_manual_broker_does_not_reacquire_slot_after_cancel():
    broker = ManualTrackBroker()
    events = []
    stopped = threading.Event()
    errors = []

    def worker():
        try:
            broker.choose("worker", [SimpleNamespace(type="video")],
                          cancelled=stopped.is_set, timeout=3,
                          on_wait=lambda: events.append("released"),
                          on_confirm=lambda: events.append("reacquired"))
        except TrackSelectionError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    for _ in range(200):
        if broker.list_pending():
            break
        time.sleep(.01)
    assert broker.list_pending()
    stopped.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors == ["Track selection cancelled"]
    assert events == ["released"]
    assert broker.list_pending() == []


def test_worker_frees_slot_for_manual_choice_and_reacquires_before_resume(monkeypatch):
    from searchapp.views import _shared

    transitions = []

    class Provider:
        def start_download(self, media_item, season=None, episodes=None):
            assert context_tracker.manual_tracks
            assert callable(context_tracker.manual_track_on_wait)
            assert callable(context_tracker.manual_track_on_confirm)
            context_tracker.manual_track_on_wait()
            assert transitions == ["acquire", "release"]
            context_tracker.manual_track_on_confirm()
            return False

    monkeypatch.setattr(_shared, "get_api", lambda site: Provider())
    monkeypatch.setattr(_shared, "_add_scheduled_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_remove_scheduled_download", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_is_scheduled_cancelled", lambda download_id: False)
    monkeypatch.setattr(_shared, "_mark_task_failed", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_log_gui_equivalent_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(_shared, "_acquire_download_slot", lambda: transitions.append("acquire"))
    monkeypatch.setattr(_shared, "_release_download_slot", lambda: transitions.append("release"))

    task = _shared._run_download_in_thread(
        "streamingcommunity", {"name": "Example", "type": "film", "slug": ""},
        manual_tracks=True, _submit=False,
    )
    with pytest.raises(RuntimeError, match="did not complete successfully"):
        task()
    assert transitions == ["acquire", "release", "acquire", "release"]
    assert context_tracker.manual_track_on_wait is None
    assert context_tracker.manual_track_on_confirm is None
    assert context_tracker.manual_tracks is False
    assert context_tracker.is_gui is False



def test_manual_picker_can_override_external_audio_and_subtitle_filters(monkeypatch, tmp_path):
    """External-track choices must reach the actual downloader dictionaries."""
    from VibraVid.core.manifest.stream import Stream
    from VibraVid.core.velora import base as base_module

    video = Stream(type="video", selected=True)

    class FakeHLSParser:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_manifest(self):
            return True

        def save_raw(self, directory):
            return tmp_path / "test.m3u8"

        def parse_streams(self):
            return [video]

    monkeypatch.setattr(base_module, "HLSParser", FakeHLSParser)
    downloader = base_module.BaseMediaDownloader.__new__(base_module.BaseMediaDownloader)
    downloader.download_id = "external-test"
    downloader.url = "https://example.invalid/test.m3u8"
    downloader.manifest_protocol = "hls"
    downloader.manifest_content = None
    downloader.headers = {}
    downloader.has_drm = False
    downloader._tmp_dir = tmp_path
    downloader.external_subtitles = [{"name": "Italian subtitle", "language": "ita"}]
    downloader.external_audios = [{"name": "English audio", "language": "eng"}]
    downloader.external_other_tracks = []
    downloader._apply_selection = lambda: None
    downloader._register_dv_companion = lambda: None
    downloader._ext_track_matches = lambda *_: False
    downloader._ext_lang_matches = lambda *_: False
    selections = []

    def choose(job_id, streams, **kwargs):
        selections.extend(streams)
        assert [stream.type for stream in streams] == ["video", "subtitle", "audio"]
        # Deselect video, explicitly select previously filtered-out external tracks.
        for stream in streams:
            stream.selected = stream.type != "video"
        return {1, 2}

    monkeypatch.setattr(base_module.manual_track_broker, "choose", choose)
    old_values = (context_tracker.is_gui, context_tracker.manual_tracks, context_tracker.defer_manual_tracks)
    old_download = context_tracker.download_id
    try:
        context_tracker.is_gui = True
        context_tracker.manual_tracks = True
        context_tracker.defer_manual_tracks = False
        context_tracker.download_id = "external-test"
        streams = downloader.parse_stream(show_table=False)
        assert len(streams) == 3
        assert len(selections) == 3
        assert [track.selected for track in streams] == [False, True, True]
        assert downloader.external_subtitles[0]["_selected"] is True
        assert downloader.external_audios[0]["_selected"] is True
    finally:
        context_tracker.is_gui, context_tracker.manual_tracks, context_tracker.defer_manual_tracks = old_values
        context_tracker.download_id = old_download
