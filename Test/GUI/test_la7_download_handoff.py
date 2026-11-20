"""Regression tests for LA7 GUI-to-provider direct download handoff."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from GUI.searchapp.api.base import Entries
from GUI.searchapp.api.la7 import La7API


def test_la7_download_passes_movie_type_without_mutating_raw_payload(monkeypatch):
    url = "https://www.la7.it/una-giornata-particolare/puntate/puntata-del-18-11-2026-399809"
    raw_data = {
        "name": "Puntata del 18-11-2026",
        "type": "tv",
        "url": url,
        "path_id": "/una-giornata-particolare",
    }
    item = Entries(name=raw_data["name"], type="movie", url=url, raw_data=raw_data)
    adapter = La7API()
    captured = []

    monkeypatch.setattr(adapter, "_get_search_fn", lambda: lambda **kwargs: captured.append(kwargs) or True)
    monkeypatch.setattr(adapter, "get_cached_scraper", lambda media: None)

    assert adapter.start_download(item) is True
    assert len(captured) == 1
    assert captured[0]["direct_item"]["type"] == "movie"
    assert captured[0]["direct_item"]["url"] == url
    assert captured[0]["direct_item"]["path_id"] == "/una-giornata-particolare"
    assert captured[0]["selections"] is None
    assert raw_data["type"] == "tv"
    assert item.raw_data is raw_data


def test_la7_download_handles_missing_raw_data(monkeypatch):
    item = Entries(
        name="LA7 episode",
        type="tv",
        url="https://www.la7.it/program/rivedila7/episode-01-01-2026-123",
    )
    adapter = La7API()
    captured = []
    monkeypatch.setattr(adapter, "_get_search_fn", lambda: lambda **kwargs: captured.append(kwargs) or True)
    monkeypatch.setattr(adapter, "get_cached_scraper", lambda media: None)

    assert adapter.start_download(item) is True
    assert captured[0]["direct_item"]["type"] == "movie"
    assert captured[0]["direct_item"]["url"] == item.url
    assert item.type == "tv"
