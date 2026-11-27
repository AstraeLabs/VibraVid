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


def test_unresolved_identity_keeps_provider_name_without_the_dub_marker():
    with mock.patch.object(anime_id_map, "resolve_tmdb_id", return_value=None), mock.patch.object(anime_id_map, "lookup", return_value=None):
        assert anime_naming.series_name_and_year("my-hero-academia-2-ita", mal_id=1) == ("my-hero-academia-2", None)
    assert anime_naming.series_name_and_year("slug") == ("slug", None)  # no ids at all: no lookups


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Vinland Saga (ITA)", "Vinland Saga"),
        ("Vinland Saga 2 (ita)", "Vinland Saga 2"),
        ("Naruto (SUB ITA)", "Naruto"),
        ("Naruto [Sub-ITA]", "Naruto"),
        ("my-hero-academia-ita", "my-hero-academia"),
        ("Overgeared", "Overgeared"),
        ("Dita", "Dita"),  # "ita" inside a word is not a marker
        ("ITA", "ITA"),  # never strip the whole name
    ],
)
def test_language_suffix_is_stripped(name, expected):
    assert anime_naming.strip_language_suffix(name) == expected


def test_imdb_gives_title_and_year_when_tmdb_has_no_key():
    record = {"themoviedb_id": {"tv": 88803}, "imdb_id": "tt10233448", "season": {"tmdb": 2}, "episode_offset": None}
    imdb = {"titleText": {"text": "Vinland Saga"}, "releaseYear": {"year": 2019}}
    with (
        mock.patch.object(type(tmdb_client), "api_key", new_callable=mock.PropertyMock, return_value=None),
        mock.patch.object(anime_id_map, "lookup", return_value=record),
        mock.patch("VibraVid.provider.imdb.IMDbClient.get_title", return_value=imdb) as get_title,
        mock.patch.object(anime_naming, "_format_has_year_token", return_value=False),
    ):
        assert anime_naming.series_name_and_year("Vinland Saga 2 (ITA)", mal_id=49387) == ("Vinland Saga (2019)", None)
    get_title.assert_called_once_with("tt10233448")


@pytest.mark.parametrize(
    "record, expected",
    [
        ({"season": {"tvdb": 2, "tmdb": 2}, "episode_offset": None}, (2, 0)),
        ({"season": {"tmdb": 3}, "episode_offset": {"tmdb": 12}}, (3, 12)),
        ({"season": None, "episode_offset": None}, (None, 0)),
        (None, (None, 0)),
    ],
)
def test_crosswalk_placement(record, expected):
    with mock.patch.object(anime_id_map, "lookup", return_value=record):
        assert anime_naming.crosswalk_placement(mal_id=49387) == expected
    assert anime_naming.crosswalk_placement() == (None, 0)


def _animeworld_series(monkeypatch, placement, episode_selection="1"):
    scrape = SimpleNamespace(get_episodes=lambda: [object()] * 24, get_name=lambda: "Vinland Saga 2 (ITA)", get_external_ids=lambda: ("49387", "136430"), session_id="s", csrf_token="c")
    seen = {}

    def fake_map(**kwargs):
        seen.update(kwargs)
        return ([], "ep")

    monkeypatch.setattr(aw, "series_name_and_year", lambda name, **_: ("Vinland Saga (2019)", None))
    monkeypatch.setattr(aw, "crosswalk_placement", lambda **_: placement)
    monkeypatch.setattr(aw, "map_episode_path", fake_map)
    monkeypatch.setattr(aw, "start_message", lambda: None)
    monkeypatch.setattr(aw, "print_episode_header", lambda *a, **k: None)
    monkeypatch.setattr(aw, "anime_folder", lambda *parts: "out")
    monkeypatch.setattr(aw.os_manager, "create_path", lambda *_: None)
    monkeypatch.setattr(aw, "VideoSource", lambda *args: SimpleNamespace(get_playlist=lambda: "url", close=lambda: None))
    monkeypatch.setattr(aw, "MP4_Downloader", lambda **kwargs: kwargs)
    aw.download_series(SimpleNamespace(url="u"), season_selection="1", episode_selection=episode_selection, scrape_serie=scrape)
    return seen


def test_animeworld_second_season_goes_in_s02_of_the_shared_folder(monkeypatch):
    seen = _animeworld_series(monkeypatch, (2, 0))
    assert (seen["series_name"], seen["season_number"], seen["episode_number"]) == ("Vinland Saga (2019)", 2, 1)


def test_animeworld_part_two_continues_the_season_numbering(monkeypatch):
    seen = _animeworld_series(monkeypatch, (3, 12))
    assert (seen["season_number"], seen["episode_number"], seen["absolute_number"]) == (3, 13, 13)


def test_animeworld_unknown_entry_keeps_the_requested_season(monkeypatch):
    seen = _animeworld_series(monkeypatch, (None, 0))
    assert (seen["season_number"], seen["episode_number"]) == (1, 1)


def test_animeunity_reads_external_ids_from_the_detail_page_when_search_has_none(monkeypatch):
    scrape = SimpleNamespace(series_name="vinland-saga-2", get_count_episodes=lambda: 1, get_external_ids=lambda: ("49387", None))
    seen = {}
    monkeypatch.setattr(au, "series_name_and_year", lambda name, **kwargs: seen.update(identity=kwargs) or ("Vinland Saga (2019)", None))
    monkeypatch.setattr(au, "crosswalk_placement", lambda **kwargs: seen.update(placement=kwargs) or (2, 0))
    monkeypatch.setattr(au, "start_message", lambda: None)
    monkeypatch.setattr(au, "VideoSourceAnime", lambda *_: object())
    monkeypatch.setattr(au, "_get_episode_by_number_or_index", lambda *args: (object(), 0))
    monkeypatch.setattr(au, "download_episode", lambda *args: seen.update(season=args[-1]) or ("output", False, None))
    title = SimpleNamespace(name="Vinland Saga 2", mal_id=None, anilist_id=None, tmdb_id=None, year="2023")
    assert au.download_series(title, season_selection="1", episode_selection="1", scrape_serie=scrape) == "output"
    assert seen["identity"]["mal_id"] == "49387" and seen["placement"]["mal_id"] == "49387" and seen["season"] == 2


def test_animeunity_scraper_parses_ids_from_detail_page(monkeypatch):
    from VibraVid.services.animeunity import scrapper

    html = '<a href="https://myanimelist.net/anime/49387/Vinland_Saga_Season_2">MAL</a><a href="https://anilist.co/anime/136430">AL</a>'
    requested = []

    class FakeClient:
        def __init__(self, **_): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            requested.append(url)
            return SimpleNamespace(text=html, raise_for_status=lambda: None)

    monkeypatch.setattr(scrapper, "create_client", FakeClient)
    scrape = scrapper.ScrapeSerieAnime("https://site.example/")
    scrape.setup(None, 1095, "vinland-saga-2")
    assert scrape.get_external_ids() == ("49387", "136430")
    assert requested == ["https://site.example/anime/1095-vinland-saga-2"]


def test_provider_year_is_used_when_identity_cannot_be_resolved():
    with mock.patch.object(anime_id_map, "resolve_tmdb_id", return_value=None), mock.patch.object(anime_id_map, "lookup", return_value=None):
        with mock.patch.object(anime_naming, "_format_has_year_token", return_value=False):
            assert anime_naming.series_name_and_year("Naruto", fallback_year="2002") == ("Naruto (2002)", None)
        with mock.patch.object(anime_naming, "_format_has_year_token", return_value=True):
            assert anime_naming.series_name_and_year("Naruto", mal_id=1, fallback_year=2002) == ("Naruto", "2002")


@pytest.mark.parametrize("year", [None, "", "0", "9999", "abcd", "20022"])
def test_invalid_provider_year_keeps_provider_name(year):
    assert anime_naming.series_name_and_year("Naruto", fallback_year=year) == ("Naruto", None)


def test_tmdb_identity_wins_over_provider_year():
    with mock.patch.object(anime_naming, "_format_has_year_token", return_value=False):
        patches = _tmdb()
        for p in patches:
            p.start()
        try:
            assert anime_naming.series_name_and_year("slug", mal_id=1, fallback_year="1999") == ("My Hero Academia (2016)", None)
        finally:
            for p in patches:
                p.stop()


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
