# 19.10.26

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from VibraVid.tui import artwork


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(artwork, "ENABLED", True)
    monkeypatch.setattr(artwork, "_url_cache", {})
    monkeypatch.setattr(artwork, "_series_ids", {})
    monkeypatch.setattr(artwork, "_bytes_cache", {})


def _png(width=40, height=60, color=(200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _item(**kw):
    base = dict(name="Title", slug="title", year=2020, tmdb_id=None, is_movie=False, is_song=False, is_album=False, poster=None, image=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_disabled_returns_nothing(monkeypatch):
    monkeypatch.setattr(artwork, "ENABLED", False)
    assert artwork.cover_url(_item(is_song=True, image="http://x/c.jpg")) is None
    assert artwork.fetch_cover("http://x/c.jpg") is None


@pytest.mark.parametrize("flag", ["is_song", "is_album"])
def test_songs_and_albums_use_the_service_cover(enabled, monkeypatch, flag):
    def boom(*a, **k):
        raise AssertionError("TMDB must not be asked for music")

    monkeypatch.setattr(artwork, "_from_tmdb", boom)
    item = _item(image="http://amazon/cover.jpg", **{flag: True})
    assert artwork.cover_url(item, "amazon_music") == "http://amazon/cover.jpg"
    assert artwork.cover_url(item, "amazon_music", own_image="http://amazon/track.jpg") == "http://amazon/track.jpg"


def test_movie_prefers_tmdb_over_the_service_image(enabled, monkeypatch):
    monkeypatch.setattr(artwork, "_from_tmdb", lambda *a: "http://tmdb/poster.jpg")
    assert artwork.cover_url(_item(is_movie=True, poster="http://site/p.jpg"), "streamingcommunity") == "http://tmdb/poster.jpg"


def test_other_providers_are_tried_when_tmdb_has_nothing(enabled, monkeypatch):
    monkeypatch.setattr(artwork, "_from_tmdb", lambda *a: None)
    monkeypatch.setattr(artwork, "_from_providers", lambda *a: "http://imdb/poster.jpg")
    assert artwork.cover_url(_item(), "x") == "http://imdb/poster.jpg"


def test_service_image_is_the_last_resort(enabled, monkeypatch):
    monkeypatch.setattr(artwork, "_from_tmdb", lambda *a: None)
    monkeypatch.setattr(artwork, "_from_providers", lambda *a: None)
    assert artwork.cover_url(_item(poster="http://site/p.jpg"), "x") == "http://site/p.jpg"
    assert artwork.cover_url(_item(name="Other"), "x", season=1, episode=2, own_image="http://site/ep.jpg") == "http://site/ep.jpg"


def test_lookup_errors_do_not_escape(enabled, monkeypatch):
    def boom(*a):
        raise RuntimeError("network down")

    monkeypatch.setattr(artwork, "_from_tmdb", boom)
    monkeypatch.setattr(artwork, "_from_providers", boom)
    assert artwork.cover_url(_item(poster="http://site/p.jpg"), "x") == "http://site/p.jpg"


def test_episode_asks_tmdb_for_season_and_episode(enabled, monkeypatch):
    seen = []

    def fake(item, site, season, episode):
        seen.append((season, episode))
        return f"http://tmdb/{season}-{episode}.jpg"

    monkeypatch.setattr(artwork, "_from_tmdb", fake)
    assert artwork.cover_url(_item(), "x") == "http://tmdb/None-None.jpg"
    assert artwork.cover_url(_item(), "x", season=2) == "http://tmdb/2-None.jpg"
    assert artwork.cover_url(_item(), "x", season=2, episode=5) == "http://tmdb/2-5.jpg"
    assert seen == [(None, None), (2, None), (2, 5)]


def test_result_is_cached(enabled, monkeypatch):
    calls = []
    monkeypatch.setattr(artwork, "_from_tmdb", lambda *a: calls.append(1) or "http://tmdb/p.jpg")
    for _ in range(3):
        artwork.cover_url(_item(), "x")
    assert len(calls) == 1


def test_render_cover_fits_the_box_and_uses_half_blocks():
    art = artwork.render_cover(_png(40, 60), 20, 8)
    assert art is not None
    lines = art.plain.split("\n")
    assert len(lines) <= 8
    assert max(len(line) for line in lines) <= 20
    assert set(art.plain) <= {"▀", "\n"}


def test_render_cover_rejects_garbage():
    assert artwork.render_cover(b"not an image", 20, 8) is None


def test_fetch_cover_caches_on_disk(enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(artwork, "_cache_file", lambda url: tmp_path / "c.img")
    hits = []

    class Response:
        status_code = 200
        content = _png()

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            hits.append(url)
            return Response()

    monkeypatch.setattr("VibraVid.utils.http_client.create_client", lambda **kw: Client())
    assert artwork.fetch_cover("http://x/c.png") == Response.content
    assert (tmp_path / "c.img").is_file()

    monkeypatch.setattr(artwork, "_bytes_cache", {})
    assert artwork.fetch_cover("http://x/c.png") == Response.content
    assert hits == ["http://x/c.png"]


def test_fetch_cover_survives_a_failing_download(enabled, monkeypatch, tmp_path):
    monkeypatch.setattr(artwork, "_cache_file", lambda url: tmp_path / "c.img")

    def boom(**kw):
        raise OSError("offline")

    monkeypatch.setattr("VibraVid.utils.http_client.create_client", boom)
    assert artwork.fetch_cover("http://x/c.png") is None
