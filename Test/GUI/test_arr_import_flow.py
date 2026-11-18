# ruff: noqa: E402

"""#789: an episode the provider does not have must stop before rescan/manual import."""

import os
import sys
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[2]
for p in (str(repo_root), str(repo_root / "GUI")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest

from searchapp import views
from searchapp.arr.downloader_service import ArrDownloaderService


class FakeSonarr:
    def __init__(self):
        self.calls = []

    def is_episode_in_queue(self, _id):
        return False

    def series_exists(self, _id):
        return True

    def command_rescan_series(self, _id):
        self.calls.append("rescan")

    def command_rename_series(self, _id):
        self.calls.append("rename")


def _service(monkeypatch, tmp_path, download_result, writes_file):
    sonarr = FakeSonarr()
    svc = ArrDownloaderService(sonarr, None)
    series_root = tmp_path / "Serie1"
    (series_root / "Season 1").mkdir(parents=True)

    for name, value in {
        "_resolve_request_tmdb_id": lambda *a, **k: 123,
        "_strict_tmdb_matching_enabled": lambda *a, **k: False,
        "_resolve_sonarr_title": lambda *a, **k: ["Serie1"],
        "_is_sonarr_episode_still_monitored": lambda *a, **k: True,
        "_search_with_fallback_titles": lambda *a, **k: ({"name": "Serie1"}, "streamingcommunity", "Serie1"),
        "_auto_map_by_episode_names": lambda *a, **k: None,
        "_provider_lacks_season": lambda *a, **k: False,
        "_sonarr_season_folder": lambda *a, **k: "Season 1",
        "_translate_path": lambda path, reverse=False: path,
        "_wait_episode_has_file": lambda *a, **k: True,
        "_warn_if_sonarr_rename_disabled": lambda *a, **k: None,
    }.items():
        monkeypatch.setattr(svc, name, value)
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    svc.manual_import_calls = []
    monkeypatch.setattr(svc, "_confirm_episode_import", lambda *a, **k: svc.manual_import_calls.append(1) or False)

    def fake_download(**kwargs):
        if writes_file:
            (Path(kwargs["output_path"]) / "Serie1 S01E13.mkv").write_bytes(b"x")
        future = Future()
        future.set_result(download_result)
        return future

    monkeypatch.setattr(views, "_run_download_in_thread", fake_download)
    serie = {
        "id": 1, "title": "Serie1", "provider": "streamingcommunity", "path": str(series_root),
        "seasons": [{"number": 1, "episodes": [{"episodeNumber": 13, "id": 99}]}],
    }
    return svc, sonarr, serie


def test_episode_missing_on_provider_skips_rescan_and_manual_import(monkeypatch, tmp_path):
    svc, sonarr, serie = _service(monkeypatch, tmp_path, download_result=False, writes_file=False)

    assert svc._process_serie_item(serie) is False
    assert svc.last_error == "episode_not_available"
    assert sonarr.calls == [] and svc.manual_import_calls == []


def test_download_that_wrote_a_file_still_goes_through_import(monkeypatch, tmp_path):
    """A provider result of False is not trusted blindly: a file on disk means the import proceeds."""
    svc, sonarr, serie = _service(monkeypatch, tmp_path, download_result=False, writes_file=True)

    assert svc._process_serie_item(serie) is True
    assert "rescan" in sonarr.calls


def test_successful_download_is_imported(monkeypatch, tmp_path):
    svc, sonarr, serie = _service(monkeypatch, tmp_path, download_result=True, writes_file=True)

    assert svc._process_serie_item(serie) is True
    assert sonarr.calls[:1] == ["rescan"] and svc.last_error is None
