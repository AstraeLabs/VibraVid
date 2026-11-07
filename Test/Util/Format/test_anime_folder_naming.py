# 01.11.26

"""#793: every season/part/dub of an anime lands in one stable "Title (Year)" folder."""

from types import SimpleNamespace
from unittest import mock

import pytest

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services.animeunity import downloader as au
from VibraVid.services.animeworld import downloader as aw
from VibraVid.services._base import tv_display_manager
from VibraVid.utils.media import anime_id_map, anime_naming


@pytest.fixture(autouse=True)
def _fresh_cache():
    anime_naming._identity_cache.clear()
    yield
    anime_naming._identity_cache.clear()


def _tmdb(title="My Hero Academia", year=2016):
    return (
        mock.patch.object(anime_id_map, "resolve_tmdb_id", return_value="65930"),
        mock.patch.object(type(tmdb_client), "api_key", new_callable=mock.PropertyMock, return_value="key"),
        mock.patch.object(tmdb_client, "get_title", return_value=title),
        mock.patch.object(tmdb_client, "get_first_air_year", return_value=year),
    )


def _resolve(**kwargs):
    patches = _tmdb()
    for p in patches:
        p.start()
    try:
        return anime_naming.series_name_and_year("my-hero-academia-2-ita", **kwargs)
    finally:
        for p in patches:
            p.stop()


def test_unresolved_identity_keeps_provider_name():
    with mock.patch.object(anime_id_map, "resolve_tmdb_id", return_value=None):
        assert anime_naming.series_name_and_year("my-hero-academia-2-ita", mal_id=1) == ("my-hero-academia-2-ita", None)
    assert anime_naming.series_name_and_year("slug") == ("slug", None)  # no ids at all: no lookups


def test_year_is_appended_when_format_has_no_year_token():
    with mock.patch.object(anime_naming, "_format_has_year_token", return_value=False):
        assert _resolve(mal_id=33486) == ("My Hero Academia (2016)", None)


def test_year_goes_to_series_year_when_format_has_the_token():
    with mock.patch.object(anime_naming, "_format_has_year_token", return_value=True):
        assert _resolve(mal_id=33486) == ("My Hero Academia", "2016")


def test_all_seasons_share_the_folder_and_lookup_is_cached():
    with mock.patch.object(anime_naming, "_format_has_year_token", return_value=False):
        first = _resolve(mal_id=1)
        second = _resolve(mal_id=1)
    assert first == second == ("My Hero Academia (2016)", None)


def test_external_ids_from_detail_page():
    html = '<a href="https://myanimelist.net/anime/33486/x">MAL</a><a href="https://anilist.co/anime/21459">AL</a>'
    assert anime_naming.external_ids_from_html(html) == ("33486", "21459")
    assert anime_naming.external_ids_from_html("") == (None, None)


def test_animeunity_uses_the_canonical_folder_for_every_season():
    scrape = SimpleNamespace(
        series_name="my-hero-academia-2-ita",
        series_identity=("My Hero Academia (2016)", None),
        is_series=True,
    )
    seen = {}

    def fake_map(**kwargs):
        seen.update(kwargs)
        return ([], "ep")

    with (
        mock.patch.object(au, "VideoSourceAnime", return_value=mock.MagicMock(master_playlist="m3u8")),
        mock.patch.object(au, "map_episode_path", side_effect=fake_map),
        mock.patch.object(au, "start_message"),
        mock.patch.object(au, "print_episode_header"),
        mock.patch.object(au, "anime_folder", return_value="out"),
        mock.patch.object(au, "HLS_Downloader"),
        mock.patch.object(au.os_manager, "get_sanitize_path", side_effect=lambda p: p),
        mock.patch.object(au.os_manager, "create_path"),
    ):
        au.download_episode(SimpleNamespace(id=1, number="3"), 2, scrape, mock.MagicMock(master_playlist="m"), season_number=2)

    assert seen["series_name"] == "My Hero Academia (2016)" and seen["season_number"] == 2


def test_animeunity_movie_uses_readable_title_and_year_in_configured_format(monkeypatch, tmp_path):
    monkeypatch.setattr(tv_display_manager, "MOVIE_FORMAT", "Films/%(title_name) (%(title_year))/%(title_name) - %(title_year)")
    monkeypatch.setattr(au, "ScrapeSerieAnime", lambda *_: mock.MagicMock(series_name="dragon-ball-broly-ita", get_info_episode=lambda _: SimpleNamespace(id=1, number=1)))
    monkeypatch.setattr(au, "VideoSourceAnime", lambda *_: mock.MagicMock(master_playlist="playlist"))
    monkeypatch.setattr(au, "start_message", lambda: None)
    monkeypatch.setattr(au, "print_episode_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(au, "movie_folder", lambda *parts: str(tmp_path.joinpath(*parts)))
    monkeypatch.setattr(au.os_manager, "create_path", lambda *_: None)
    captured = {}
    def downloader(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(start=lambda: "done")
    monkeypatch.setattr(au, "HLS_Downloader", downloader)
    title = SimpleNamespace(id=1, slug="dragon-ball-broly-ita", name="Dragon Ball Super Broly", year="2018")
    assert au.download_film(title) == "done"
    assert captured["output_path"] == str(tmp_path / "Films" / "Dragon Ball Super Broly (2018)" / f"Dragon Ball Super Broly - 2018.{au.extension_output}")


def test_animeworld_movie_uses_configured_movie_format(monkeypatch, tmp_path):
    monkeypatch.setattr(tv_display_manager, "MOVIE_FORMAT", "Films/%(title_name) (%(title_year))/%(title_name) - %(title_year)")
    scrape = SimpleNamespace(get_episodes=lambda: [object()], get_name=lambda: "Dragon Ball Super Broly", session_id="s", csrf_token="c")
    monkeypatch.setattr(aw, "ScrapSerie", lambda *args: scrape)
    monkeypatch.setattr(aw, "VideoSource", lambda *args: SimpleNamespace(get_playlist=lambda: "url", close=lambda: None))
    monkeypatch.setattr(aw, "start_message", lambda: None)
    monkeypatch.setattr(aw, "anime_folder", lambda *parts: str(tmp_path.joinpath(*parts)))
    monkeypatch.setattr(aw.os_manager, "create_path", lambda *_: None)
    monkeypatch.setattr(aw, "MP4_Downloader", lambda **kwargs: kwargs)
    result = aw.download_film(SimpleNamespace(url="url", year="2018"))
    assert result["path"] == str(tmp_path / "Films" / "Dragon Ball Super Broly (2018)" / "Dragon Ball Super Broly - 2018.mp4")


def test_animeunity_series_uses_readable_title_as_identity_fallback(monkeypatch):
    scrape = SimpleNamespace(series_name="show-slug", get_count_episodes=lambda: 1)
    seen = {}
    def identity(name, **kwargs):
        seen["name"] = name
        return name, None
    monkeypatch.setattr(au, "series_name_and_year", identity)
    monkeypatch.setattr(au, "start_message", lambda: None)
    monkeypatch.setattr(au, "VideoSourceAnime", lambda *_: object())
    monkeypatch.setattr(au, "_get_episode_by_number_or_index", lambda *args: (object(), 0))
    monkeypatch.setattr(au, "download_episode", lambda *args: ("output", False, None))
    title = SimpleNamespace(name="Readable Show", mal_id=None, anilist_id=None, tmdb_id=None)
    assert au.download_series(title, episode_selection="1", scrape_serie=scrape) == "output"
    assert seen["name"] == "Readable Show"
