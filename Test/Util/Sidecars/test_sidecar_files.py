# 01.10.26
# ruff: noqa: E402

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.services._base import sidecars as sidecars_module
from VibraVid.services._base.metadata import BaseMetadataProvider, EpisodeInfo, MovieInfo, Person, SeriesInfo
from VibraVid.services._base.sidecars import Sidecars

JPEG = bytes([0xFF, 0xD8, 0xFF]) + b"jpeg"
WEBP = b"RIFF\x00\x00\x00\x00WEBP"


class _Images:
    """create_client() stand-in: records every URL, serves JPEG (or whatever `content` says)."""

    def __init__(self):
        self.content, self.urls = JPEG, []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        self.urls.append(url)
        return SimpleNamespace(status_code=200, content=self.content)


class _Stub(BaseMetadataProvider):
    NAME = "tmdb"

    def __init__(self):
        self.movie_info = MovieInfo(
            title="Matrix", original_title="The Matrix", year=1999, premiered="1999-03-31", plot="Trama", tagline="Credete.", runtime=136,
            genres=["Azione", "Fantascienza"], rating=8.26, votes=28849, mpaa="T", tags=["hacker", "ai"], countries=["USA"],
            studios=["Silver Pictures", "Warner"], collection="Matrix - Collezione", collection_overview="La saga.",
            directors=["Lana", "Lilly"], writers=["Lana"], actors=[Person("Keanu", "Neo", "https://i/k.jpg"), Person("Laurence", "Morpheus")],
            trailer_url="https://www.youtube.com/watch?v=abc", ids={"tmdb": "603", "imdb": "tt0133093"},
            image_url="https://i/poster.jpg", fanart_url="https://i/fanart.jpg",
        )
        self.series_info = SeriesInfo(
            title="Delitti in Paradiso", original_title="Death in Paradise", year=2011, premiered="2011-10-25", status="Continuing",
            plot="Serie", mpaa="TV-14", runtime=53, rating=7.4, votes=344, genres=["Giallo"], studios=["BBC One"],
            actors=[Person("Don", "Selwyn")], ids={"tmdb": "41956", "imdb": "tt1888075", "tvdb": "252800"},
            image_url="https://i/series.jpg", fanart_url="https://i/series-bg.jpg",
            season_posters={1: "https://i/s1.jpg", 15: "https://i/s15.jpg"},
        )
        self.series_calls = 0
        self.fail_series = False

    def episode_info(self, season=15, episode=1):
        return EpisodeInfo(
            title="Corruzione a Saint Marie", show_title="Delitti in Paradiso", season=season, episode=episode, plot="Ep", aired="2026-01-30",
            runtime=59, rating=6.2, votes=12, directors=["John"], writers=["James"], actors=[Person("Guest", "Mr X", "https://i/g.jpg")],
            ids={"tmdb": "6854734"}, image_url="https://i/still.jpg",
        )

    def movie(self, item_id):
        return self.movie_info

    def episode(self, series_id, season, episode):
        return self.episode_info(season, episode)

    def series(self, series_id):
        self.series_calls += 1
        if self.fail_series:
            raise RuntimeError("series lookup down")
        return self.series_info


@pytest.fixture
def stub(monkeypatch):
    provider = _Stub()
    monkeypatch.setattr(sidecars_module, "get_provider", lambda name: provider)
    return provider


@pytest.fixture
def images(monkeypatch):
    fake = _Images()
    monkeypatch.setattr(sidecars_module, "create_client", fake)
    return fake


def _video(tmp_path, *parts):
    path = tmp_path.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return str(path)


def _names(paths):
    return sorted(Path(p).name for p in paths)


def _root(path):
    return ET.parse(path).getroot()


# ── film ──────────────────────────────────────────────────────────────────

def test_movie_nfo_has_every_field_of_the_kodi_model(tmp_path, stub, images):
    video = _video(tmp_path, "Matrix (1999).mkv")
    Sidecars(video, "tmdb", "movie", "603").write()
    root = _root(tmp_path / "Matrix (1999).nfo")

    assert root.tag == "movie"
    assert [root.findtext(t) for t in ("title", "originaltitle", "sorttitle", "year", "premiered")] == ["Matrix", "The Matrix", "Matrix", "1999", "1999-03-31"]
    assert (root.findtext("tagline"), root.findtext("plot"), root.findtext("runtime"), root.findtext("mpaa")) == ("Credete.", "Trama", "136", "T")
    rating = root.find("ratings/rating")
    assert rating.attrib == {"name": "themoviedb", "max": "10", "default": "true"}
    assert (rating.findtext("value"), rating.findtext("votes"), root.findtext("rating")) == ("8.3", "28849", "8.3")
    assert [(u.get("type"), u.text, u.get("default")) for u in root.findall("uniqueid")] == [("tmdb", "603", "true"), ("imdb", "tt0133093", None)]
    assert root.findtext("id") == "tt0133093" and root.findtext("tmdbid") == "603"
    assert [g.text for g in root.findall("genre")] == ["Azione", "Fantascienza"] and [t.text for t in root.findall("tag")] == ["hacker", "ai"]
    assert root.findtext("country") == "USA" and [s.text for s in root.findall("studio")] == ["Silver Pictures", "Warner"]
    assert (root.findtext("set/name"), root.findtext("set/overview")) == ("Matrix - Collezione", "La saga.")
    assert [d.text for d in root.findall("director")] == ["Lana", "Lilly"] and [c.text for c in root.findall("credits")] == ["Lana"]
    assert root.find("thumb").attrib == {"aspect": "poster"} and root.findtext("thumb") == "https://i/poster.jpg"
    assert root.findtext("fanart/thumb") == "https://i/fanart.jpg" and root.findtext("trailer") == "https://www.youtube.com/watch?v=abc"
    actors = root.findall("actor")
    assert [(a.findtext("name"), a.findtext("role"), a.findtext("order"), a.findtext("thumb")) for a in actors] == [
        ("Keanu", "Neo", "0", "https://i/k.jpg"), ("Laurence", "Morpheus", "1", None),
    ]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", root.findtext("dateadded"))


def test_movie_writes_poster_and_fanart_images(tmp_path, stub, images):
    video = _video(tmp_path, "Matrix (1999).mkv")
    written = Sidecars(video, "tmdb", "movie", "603").write()
    assert _names(written) == ["Matrix (1999)-fanart.jpg", "Matrix (1999)-poster.jpg", "Matrix (1999).nfo"]
    assert images.urls == ["https://i/poster.jpg", "https://i/fanart.jpg"]


def test_movie_without_fanart_writes_no_fanart_file(tmp_path, stub, images):
    stub.movie_info.fanart_url = None
    written = Sidecars(_video(tmp_path, "m.mkv"), "tmdb", "movie", "1").write()
    assert _names(written) == ["m-poster.jpg", "m.nfo"]
    assert _root(tmp_path / "m.nfo").find("fanart") is None


def test_empty_values_never_produce_empty_tags(tmp_path, stub, images):
    stub.movie_info = MovieInfo(title="Bare")
    Sidecars(_video(tmp_path, "b.mkv"), "tmdb", "movie", "1").write()
    root = _root(tmp_path / "b.nfo")
    assert [c.tag for c in root] == ["title", "sorttitle", "dateadded"]


@pytest.mark.parametrize("provider,name", [("tmdb", "themoviedb"), ("imdb", "imdb"), ("tvdb", "thetvdb")])
def test_rating_is_named_after_its_provider(tmp_path, stub, images, provider, name):
    stub.movie_info.ids = {provider: "1"}
    Sidecars(_video(tmp_path, "r.mkv"), provider, "movie", "1").write()
    assert _root(tmp_path / "r.nfo").find("ratings/rating").get("name") == name


def test_no_rating_block_without_a_rating(tmp_path, stub, images):
    stub.movie_info.rating = None
    Sidecars(_video(tmp_path, "n.mkv"), "tmdb", "movie", "1").write()
    root = _root(tmp_path / "n.nfo")
    assert root.find("ratings") is None and root.find("rating") is None


# ── episode ───────────────────────────────────────────────────────────────

def test_episode_nfo_has_every_field_of_the_kodi_model(tmp_path, stub, images):
    video = _video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv")
    Sidecars(video, "tmdb", "tv", "41956", 15, 1).write()
    root = _root(tmp_path / "Delitti in Paradiso" / "S15" / "ep.nfo")

    assert root.tag == "episodedetails"
    assert [root.findtext(t) for t in ("title", "showtitle", "season", "episode", "aired", "plot", "runtime")] == [
        "Corruzione a Saint Marie", "Delitti in Paradiso", "15", "1", "2026-01-30", "Ep", "59",
    ]
    assert (root.findtext("ratings/rating/value"), root.findtext("ratings/rating/votes")) == ("6.2", "12")
    assert [d.text for d in root.findall("director")] == ["John"] and [c.text for c in root.findall("credits")] == ["James"]
    assert root.findtext("thumb") == "https://i/still.jpg"
    assert [(a.findtext("name"), a.findtext("role")) for a in root.findall("actor")] == [("Guest", "Mr X")]


def test_episode_writes_its_thumb(tmp_path, stub, images):
    video = _video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv")
    written = Sidecars(video, "tmdb", "tv", "41956", 15, 1).write()
    assert "ep-thumb.jpg" in _names(written) and "ep.nfo" in _names(written)


# ── series files ──────────────────────────────────────────────────────────

def test_first_episode_writes_the_show_files(tmp_path, stub, images):
    video = _video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv")
    written = Sidecars(video, "tmdb", "tv", "41956", 15, 1).write()
    show = tmp_path / "Delitti in Paradiso"

    assert _names(written) == ["ep-thumb.jpg", "ep.nfo", "fanart.jpg", "poster.jpg", "season15-poster.jpg", "tvshow.nfo"]
    assert all((show / name).exists() for name in ("tvshow.nfo", "poster.jpg", "fanart.jpg", "season15-poster.jpg"))
    root = _root(show / "tvshow.nfo")
    assert root.tag == "tvshow"
    assert [root.findtext(t) for t in ("title", "originaltitle", "showtitle", "sorttitle", "year", "premiered", "status", "mpaa", "runtime")] == [
        "Delitti in Paradiso", "Death in Paradise", "Delitti in Paradiso", "Delitti in Paradiso", "2011", "2011-10-25", "Continuing", "TV-14", "53",
    ]
    assert [(u.get("type"), u.text) for u in root.findall("uniqueid")] == [("tmdb", "41956"), ("imdb", "tt1888075"), ("tvdb", "252800")]
    assert [g.text for g in root.findall("genre")] == ["Giallo"] and [s.text for s in root.findall("studio")] == ["BBC One"]
    thumbs = [(t.get("aspect"), t.get("season"), t.text) for t in root.findall("thumb")]
    assert thumbs == [("poster", "-1", "https://i/series.jpg"), ("poster", "1", "https://i/s1.jpg"), ("poster", "15", "https://i/s15.jpg")]
    assert root.findtext("fanart/thumb") == "https://i/series-bg.jpg" and root.findtext("actor/name") == "Don"


def test_show_files_are_written_only_once(tmp_path, stub, images):
    first = _video(tmp_path, "Delitti in Paradiso", "S15", "ep1.mkv")
    second = _video(tmp_path, "Delitti in Paradiso", "S15", "ep2.mkv")
    Sidecars(first, "tmdb", "tv", "41956", 15, 1).write()
    images.urls.clear()
    written = Sidecars(second, "tmdb", "tv", "41956", 15, 2).write()

    assert stub.series_calls == 1  # no second lookup, no second download
    assert _names(written) == ["ep2-thumb.jpg", "ep2.nfo"]
    assert images.urls == ["https://i/still.jpg"]


def test_an_existing_tvshow_nfo_is_never_overwritten(tmp_path, stub, images):
    show = tmp_path / "Delitti in Paradiso"
    show.mkdir()
    (show / "tvshow.nfo").write_text("<tvshow><title>Mine</title></tvshow>", encoding="utf-8")
    video = _video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv")

    written = Sidecars(video, "tmdb", "tv", "41956", 15, 1).write()

    assert "tvshow.nfo" not in _names(written) and (show / "tvshow.nfo").read_text(encoding="utf-8") == "<tvshow><title>Mine</title></tvshow>"
    assert {"poster.jpg", "fanart.jpg", "season15-poster.jpg"} <= set(_names(written))  # the missing artwork is still filled in


def test_a_new_season_only_adds_its_poster(tmp_path, stub, images):
    Sidecars(_video(tmp_path, "Show Name", "S15", "a.mkv"), "tmdb", "tv", "1", 15, 1).write()
    stub.series_info.title = "Show Name"
    written = Sidecars(_video(tmp_path, "Show Name", "S01", "b.mkv"), "tmdb", "tv", "1", 1, 1).write()
    assert set(_names(written)) == {"season01-poster.jpg", "b.nfo", "b-thumb.jpg"}  # tvshow.nfo, poster and fanart already exist
    assert stub.series_calls == 2


def test_specials_use_the_kodi_specials_poster_name(tmp_path, stub, images):
    stub.series_info.season_posters[0] = "https://i/sp.jpg"
    written = Sidecars(_video(tmp_path, "Delitti in Paradiso", "Specials", "x.mkv"), "tmdb", "tv", "1", 1, 1)
    written._write_series(stub, stub.episode_info(season=0, episode=1))
    assert "season-specials-poster.jpg" in _names(written.written)


@pytest.mark.parametrize("season_dir", ["S15", "S1", "Season 1", "season 01", "Stagione 02", "Saison 3", "Staffel 4", "Specials", "s01", "Season.2", "Season_3"])
def test_season_folders_are_recognised(tmp_path, stub, images, season_dir):
    video = _video(tmp_path, "Any Show", season_dir, "ep.mkv")
    written = Sidecars(video, "tmdb", "tv", "1", 1, 1).write()
    assert "tvshow.nfo" in _names(written) and (tmp_path / "Any Show" / "tvshow.nfo").exists()


def test_episodes_straight_in_the_show_folder(tmp_path, stub, images):
    video = _video(tmp_path, "Delitti in Paradiso", "ep.mkv")
    written = Sidecars(video, "tmdb", "tv", "1", 15, 1).write()
    assert "tvshow.nfo" in _names(written) and (tmp_path / "Delitti in Paradiso" / "tvshow.nfo").exists()


def test_an_unrecognised_folder_gets_no_show_files_but_the_episode_still_does(tmp_path, stub, images):
    video = _video(tmp_path, "Downloads", "ep.mkv")  # neither a season folder nor named like the show
    written = Sidecars(video, "tmdb", "tv", "1", 15, 1).write()

    assert _names(written) == ["ep-thumb.jpg", "ep.nfo"] and stub.series_calls == 0
    assert not (tmp_path / "tvshow.nfo").exists() and not (tmp_path / "Downloads" / "tvshow.nfo").exists()


def test_show_files_are_not_written_for_a_rejected_candidate(tmp_path, stub, images):
    video = _video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv")
    written = Sidecars(video, "tmdb", "tv", "1", 15, 1, episode_name="Un altro episodio", verify=True).write()
    assert written == [] and stub.series_calls == 0


def test_show_files_follow_a_confirmed_candidate(tmp_path, stub, images):
    video = _video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv")
    written = Sidecars(video, "tmdb", "tv", "1", 15, 1, episode_name="Corruzione a Saint Marie", verify=True).write()
    assert "tvshow.nfo" in _names(written)


def test_a_failing_series_lookup_keeps_the_episode_files(tmp_path, stub, images):
    stub.fail_series = True
    video = _video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv")
    written = Sidecars(video, "tmdb", "tv", "1", 15, 1).write()
    assert _names(written) == ["ep-thumb.jpg", "ep.nfo"]


def test_a_missing_season_poster_is_just_skipped(tmp_path, stub, images):
    stub.series_info.season_posters = {}
    written = Sidecars(_video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv"), "tmdb", "tv", "1", 15, 1).write()
    assert "season15-poster.jpg" not in _names(written) and "tvshow.nfo" in _names(written)


def test_unusable_show_artwork_does_not_stop_the_nfo(tmp_path, stub, images):
    images.content = WEBP
    written = Sidecars(_video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv"), "tmdb", "tv", "1", 15, 1).write()
    assert set(_names(written)) == {"ep.nfo", "tvshow.nfo"}


def test_no_temporary_files_are_left_behind(tmp_path, stub, images):
    Sidecars(_video(tmp_path, "Delitti in Paradiso", "S15", "ep.mkv"), "tmdb", "tv", "1", 15, 1).write()
    assert not list(tmp_path.rglob("*.tmp"))
