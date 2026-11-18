# ruff: noqa: E402
"""Selected-track GUI metadata regression tests, without provider requests."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from VibraVid.core.ui.manual_tracks import selected_track_metadata
from VibraVid.core.ui.tracker import DownloadTracker


ROOT = Path(__file__).resolve().parents[2]


def test_selected_tracks_whitelist_and_normalized_language():
    items = [
        SimpleNamespace(type="video", selected=True, width=1920, height=1080,
                        codecs="avc1", bitrate=4000000, url="https://example.test/?secret=ABC"),
        SimpleNamespace(type="audio", selected=True, language="it", resolved_language="it-IT",
                        codecs="eac3", channels="6", cookies={"token": "SECRET"}),
        SimpleNamespace(type="subtitle", selected=True, language="eng", forced=True, is_sdh=True),
        SimpleNamespace(type="audio", selected=False, language="fr", codecs="aac"),
        SimpleNamespace(type="image", selected=True, language="und"),
    ]
    result = selected_track_metadata(items)
    assert [row["type"] for row in result] == ["video", "audio", "subtitle"]
    assert result[1]["language"] == "it-IT"
    assert result[2]["forced"] and result[2]["sdh"]
    payload = json.dumps(result)
    assert "SECRET" not in payload and "ABC" not in payload
    assert "https:" not in payload and "cookies" not in payload
    assert "selected" not in result[0] and "index" not in result[0]


def test_selected_tracks_do_not_expose_undetermined_media_as_known_language():
    result = selected_track_metadata([SimpleNamespace(type="audio", selected=True, language="und")])
    assert result[0]["language"] == "und"


def test_metadata_updates_existing_job_without_mutating_other_jobs():
    tracker = SimpleNamespace(
        downloads={"A": {"selected_tracks": []}, "B": {"selected_tracks": []}},
        _lock=threading.Lock(),
    )
    tracks = [SimpleNamespace(type="audio", selected=True, language="ita")]
    DownloadTracker.update_selected_tracks(tracker, "A", tracks)
    assert tracker.downloads["A"]["selected_tracks"][0]["language"] == "ita"
    assert tracker.downloads["B"]["selected_tracks"] == []
    DownloadTracker.update_selected_tracks(tracker, "missing", tracks)
    DownloadTracker.update_selected_tracks(tracker, "A", [])
    assert tracker.downloads["A"]["selected_tracks"] == []


def test_metadata_is_bounded_and_json_serializable():
    tracks = [SimpleNamespace(type="audio", selected=True, language="it") for _ in range(100)]
    selected = selected_track_metadata(tracks)
    assert len(selected) == 48
    assert json.loads(json.dumps(selected)) == selected


def test_gui_exposes_metadata_in_active_and_history_without_showing_fictional_audio():
    template = (ROOT / "GUI/searchapp/templates/searchapp/cinema_download.html").read_text()
    assert "selectedTrackFacts(item)" in template
    assert "selectedTrackFacts(d)" in template
    assert 'JSON.stringify(d.selected_tracks || [])' in template
    assert "readableLanguage(track.language)" in template
    assert 'esc(values.join(" | "))' in template
    assert 'esc(short)' in template


def test_catalog_label_is_explicitly_not_verified_audio():
    template = (ROOT / "GUI/searchapp/templates/searchapp/cinema_results.html").read_text()
    assert 'title="Provider catalog language, not verified audio"' in template
    assert "result.catalog_language" in template


def test_track_metadata_publication_follows_final_label_preparation():
    """Both manifest orchestration paths publish only after label preparation."""
    velora = (ROOT / "VibraVid/core/velora/downloader.py").read_text()
    assert "self._prepare_labels()\n        if self.download_id:\n" in velora

    generic = (ROOT / "VibraVid/core/downloader/_generic.py").read_text()
    expected = (
        "            md._prepare_labels()\n\n"
        "        if self.download_id:\n"
        "            download_tracker.update_selected_tracks("
    )
    assert generic.count(expected) == 2
