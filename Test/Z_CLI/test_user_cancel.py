# 07.10.26

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.core.downloader.base import DownloadCancelled
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import tmdb_artwork, tv_download_manager
from VibraVid.services._base.site_search_manager import _handle_download_result


@pytest.fixture
def scrape_serie():
    class _Scrape:
        def getEpisodeSeasons(self, season):
            assert season == 1
            return [{"number": 1, "name": "ep1"}]

    return _Scrape()


@pytest.fixture(autouse=True)
def _no_artwork_network(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "resolve_episode_artwork_url", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _clean_context():
    prev_id = context_tracker.download_id
    context_tracker.download_id = None
    try:
        yield
    finally:
        context_tracker.download_id = prev_id


def _callback(result):
    def _run(episode, season, number):
        return result

    return _run


def test_cancelled_episode_raises_download_cancelled_not_runtime_error(scrape_serie):
    with pytest.raises(DownloadCancelled):
        tv_download_manager.process_episode_download(
            index_season_selected=1,
            scrape_serie=scrape_serie,
            download_video_callback=_callback((None, True, "cancelled")),
            download_all=True,
        )


def test_cancelled_episode_in_selection_mode_raises_download_cancelled(scrape_serie):
    with pytest.raises(DownloadCancelled):
        tv_download_manager.process_episode_download(
            index_season_selected=1,
            scrape_serie=scrape_serie,
            download_video_callback=_callback((None, True, "cancelled")),
            download_all=False,
            episode_selection="1",
        )


def test_genuine_failure_still_raises_runtime_error(scrape_serie):
    with pytest.raises(RuntimeError, match="failed for season 1"):
        tv_download_manager.process_episode_download(
            index_season_selected=1,
            scrape_serie=scrape_serie,
            download_video_callback=_callback((None, True, "boom")),
            download_all=True,
        )


def test_handle_download_result_raises_on_cancelled():
    with pytest.raises(DownloadCancelled):
        _handle_download_result((None, True, "cancelled"))


def test_handle_download_result_prints_other_errors(capsys):
    _handle_download_result((None, True, "boom"))
    assert "boom" in capsys.readouterr().out
