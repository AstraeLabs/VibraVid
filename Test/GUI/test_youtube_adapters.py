# 29.10.26
# ruff: noqa: E402

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
gui_dir = repo_root / "GUI"
for p in (str(repo_root), str(gui_dir)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest

from searchapp import api
from searchapp.api.base import Entries

from VibraVid.services import youtube as youtube_service
from VibraVid.services._base.object import Entries as ServiceEntries

VIDEO = {"id": "cvzXYykLmGI", "title": "Siberia EP.3", "uploader": "Jaser", "duration": 1810, "thumbnail": "https://i.ytimg.com/x.jpg"}


@pytest.mark.parametrize("site", ["youtube", "youtube_music"])
def test_adapters_are_registered(site):
    assert site in api.get_available_sites()
    assert api.get_api(site).site_name == site


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=cvzXYykLmGI",
        "https://www.youtube.com/watch?v=cvzXYykLmGI&list=PLabc&t=30s",
        "https://youtu.be/cvzXYykLmGI?si=zzz",
        "https://www.youtube.com/shorts/cvzXYykLmGI",
        "https://www.youtube.com/embed/cvzXYykLmGI",
    ],
)
def test_url_resolves_to_a_single_video(monkeypatch, url):
    asked = []
    monkeypatch.setattr(youtube_service.YTDLPDownloader, "extract_info", lambda target, **kw: asked.append(target) or VIDEO)

    item = youtube_service._resolve_url_to_item(url)

    assert asked == ["https://www.youtube.com/watch?v=cvzXYykLmGI"]  # playlist/time params never reach yt-dlp
    assert item["id"] == "cvzXYykLmGI" and item["type"] == "film"
    assert item["url"] == "https://www.youtube.com/watch?v=cvzXYykLmGI"
    ServiceEntries(**item)  # the download path rebuilds the Entries from this dict


def test_non_video_urls_fall_back_to_text_search(monkeypatch):
    monkeypatch.setattr(youtube_service.YTDLPDownloader, "extract_info", lambda *a, **kw: pytest.fail("must not be called"))
    assert youtube_service._resolve_url_to_item("https://www.youtube.com/@somechannel") is None
    assert youtube_service._resolve_url_to_item("") is None


def test_adapter_search_pasted_url_returns_the_video(monkeypatch):
    monkeypatch.setattr(youtube_service.YTDLPDownloader, "extract_info", lambda target, **kw: VIDEO)

    results = api.get_api("youtube").search("https://www.youtube.com/watch?v=cvzXYykLmGI")

    assert len(results) == 1 and isinstance(results[0], Entries)
    assert results[0].is_movie and results[0].name == "Jaser - Siberia EP.3"
    assert results[0].raw_data["duration_seconds"] == 1810


def test_music_songs_have_no_tracklist():
    song = Entries(id="x", name="A - B", type="song", url="https://music.youtube.com/watch?v=x")
    assert api.get_api("youtube_music").get_series_metadata(song) is None
