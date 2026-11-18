# 01.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base.metadata import Person
from VibraVid.services._base.metadata.imdb import ImdbProvider
from VibraVid.services._base.metadata.tmdb import TmdbProvider
from VibraVid.services._base.metadata.tvdb import TvdbProvider

# ── TMDB ──────────────────────────────────────────────────────────────────

TMDB_MOVIE = {
    "title": "Matrix", "original_title": "The Matrix", "release_date": "1999-03-31", "overview": "Trama", "tagline": "Credete.",
    "runtime": 136, "vote_average": 8.26, "vote_count": 28849, "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
    "genres": [{"name": "Azione"}], "imdb_id": "tt0133093",
    "production_countries": [{"name": "United States of America"}],
    "production_companies": [{"name": "Silver Pictures"}, {"name": "Warner Bros."}],
    "belongs_to_collection": {"id": 2344, "name": "Matrix - Collezione"},
    "keywords": {"keywords": [{"name": "hacker"}, {"name": "simulated reality"}]},
    "release_dates": {"results": [
        {"iso_3166_1": "US", "release_dates": [{"certification": "R"}]},
        {"iso_3166_1": "IT", "release_dates": [{"certification": ""}, {"certification": "T"}]},
    ]},
    "videos": {"results": [{"type": "Teaser", "site": "YouTube", "key": "tea"}, {"type": "Trailer", "site": "Vimeo", "key": "vim"}, {"type": "Trailer", "site": "YouTube", "key": "ECEg"}]},
    "credits": {
        "cast": [{"name": "Second", "character": "Morpheus", "order": 1, "profile_path": None}, {"name": "Keanu", "character": "Neo", "order": 0, "profile_path": "/k.jpg"}],
        "crew": [
            {"name": "Lana", "job": "Director"}, {"name": "Lana", "job": "Writer"}, {"name": "Lilly", "job": "Screenplay"},
            {"name": "Joel", "job": "Producer"}, {"name": "Lana", "job": "Director"},
        ],
    },
}


def _tmdb(monkeypatch, responses):
    monkeypatch.setattr(tmdb_client, "_make_request", lambda endpoint, params=None, retries=3: dict(responses.get(endpoint, {})))
    monkeypatch.setattr(tmdb_client, "_api_key", "k")


def test_tmdb_movie_carries_the_full_record(monkeypatch):
    _tmdb(monkeypatch, {"movie/603": TMDB_MOVIE, "collection/2344": {"overview": "La saga."}})
    info = TmdbProvider().movie("603")

    assert (info.rating, info.votes, info.mpaa) == (8.26, 28849, "T")  # Italian certification wins, empty ones are skipped
    assert info.tags == ["hacker", "simulated reality"] and info.countries == ["United States of America"]
    assert info.studios == ["Silver Pictures", "Warner Bros."]
    assert (info.collection, info.collection_overview) == ("Matrix - Collezione", "La saga.")
    assert info.directors == ["Lana"]  # duplicates collapsed
    assert info.writers == ["Lana", "Lilly"]  # Writer and Screenplay, producers ignored
    assert [(a.name, a.role) for a in info.actors] == [("Keanu", "Neo"), ("Second", "Morpheus")]  # by billing order
    assert info.actors[0].image_url == "https://image.tmdb.org/t/p/w185/k.jpg" and info.actors[1].image_url is None
    assert info.trailer_url == "https://www.youtube.com/watch?v=ECEg"  # only a YouTube trailer counts
    assert info.fanart_url == "https://image.tmdb.org/t/p/w1280/b.jpg" and info.ids == {"tmdb": "603", "imdb": "tt0133093"}


def test_tmdb_movie_without_collection_or_certification(monkeypatch):
    _tmdb(monkeypatch, {"movie/1": {"title": "X", "release_dates": {"results": [{"iso_3166_1": "US", "release_dates": [{"certification": "PG"}]}]}}})
    info = TmdbProvider().movie("1")
    assert (info.collection, info.collection_overview, info.mpaa) == (None, None, "PG")  # falls back to the US rating


def test_tmdb_episode_has_crew_guests_and_rating(monkeypatch):
    monkeypatch.setattr(tmdb_client, "resolve_actual_season_episode", lambda tid, s, e: (s, e))
    _tmdb(monkeypatch, {
        "tv/1": {"name": "Show"},
        "tv/1/season/2/episode/3": {
            "id": 77, "name": "Ep", "vote_average": 6.2, "vote_count": 12, "still_path": "/s.jpg",
            "crew": [{"name": "Dir", "job": "Director"}, {"name": "Wri", "job": "Writer"}],
            "guest_stars": [{"name": "Guest", "character": "Mr X", "profile_path": "/g.jpg"}],
        },
    })
    info = TmdbProvider().episode("1", 2, 3)
    assert (info.rating, info.votes, info.directors, info.writers) == (6.2, 12, ["Dir"], ["Wri"])
    assert info.actors == [Person("Guest", "Mr X", "https://image.tmdb.org/t/p/w185/g.jpg")]


def test_tmdb_series_record(monkeypatch):
    _tmdb(monkeypatch, {"tv/9": {
        "name": "Delitti in Paradiso", "original_name": "Death in Paradise", "first_air_date": "2011-10-25", "status": "Returning Series",
        "overview": "Trama", "episode_run_time": [53], "vote_average": 7.4, "vote_count": 344,
        "genres": [{"name": "Giallo"}], "networks": [{"name": "BBC One"}], "poster_path": "/p.jpg", "backdrop_path": "/b.jpg",
        "external_ids": {"imdb_id": "tt1888075", "tvdb_id": 252800},
        "content_ratings": {"results": [{"iso_3166_1": "GB", "rating": "12"}, {"iso_3166_1": "US", "rating": "TV-14"}]},
        "credits": {"cast": [{"name": "Don", "character": "Selwyn", "order": 0}]},
        "seasons": [{"season_number": 0, "poster_path": None}, {"season_number": 15, "poster_path": "/s15.jpg"}],
    }})
    info = TmdbProvider().series("9")

    assert (info.title, info.original_title, info.year, info.status, info.runtime) == ("Delitti in Paradiso", "Death in Paradise", 2011, "Continuing", 53)
    assert info.mpaa == "TV-14" and info.studios == ["BBC One"] and info.genres == ["Giallo"]  # no Italian rating: the US one
    assert info.ids == {"tmdb": "9", "imdb": "tt1888075", "tvdb": "252800"}
    assert info.season_posters == {15: "https://image.tmdb.org/t/p/w780/s15.jpg"}  # seasons without a poster are left out
    assert [a.name for a in info.actors] == ["Don"]


def test_tmdb_series_status_mapping(monkeypatch):
    for tmdb_status, expected in (("Ended", "Ended"), ("Canceled", "Ended"), ("In Production", "Continuing"), (None, None)):
        _tmdb(monkeypatch, {"tv/1": {"name": "S", "status": tmdb_status}})
        assert TmdbProvider().series("1").status == expected


def test_tmdb_series_unknown_id_is_none(monkeypatch):
    _tmdb(monkeypatch, {})
    assert TmdbProvider().series("404") is None


# ── IMDb ──────────────────────────────────────────────────────────────────

def _edge(**node):
    return {"edges": [{"node": node}]}


IMDB_RICH = {
    "id": "tt0133093", "titleText": {"text": "Matrix"}, "originalTitleText": {"text": "The Matrix"}, "releaseYear": {"year": 1999},
    "releaseDate": {"year": 1999, "month": 3, "day": 31}, "plot": {"plotText": {"plainText": "Trama"}},
    "primaryImage": {"url": "https://img/p.jpg"}, "runtime": {"seconds": 8160}, "genres": {"genres": [{"text": "Action"}]},
    "ratingsSummary": {"aggregateRating": 8.7, "voteCount": 2282440}, "certificate": {"rating": "R"},
    "countriesOfOrigin": {"countries": [{"text": "United States"}]},
    "taglines": _edge(text="Free your mind"),
    "keywords": {"edges": [{"node": {"keyword": {"text": {"text": "simulated reality"}}}}, {"node": {"keyword": {"text": {"text": "hacker"}}}}]},
    "companyCredits": _edge(company={"companyText": {"text": "Warner Bros."}}),
    "directors": {"edges": [{"node": {"name": {"nameText": {"text": "Lana"}}}}, {"node": {"name": {"nameText": {"text": "Lana"}}}}]},
    "writers": _edge(name={"nameText": {"text": "Lilly"}}),
    "cast": {"edges": [
        {"node": {"name": {"nameText": {"text": "Keanu"}, "primaryImage": {"url": "https://img/k.jpg"}}, "characters": [{"name": "Neo"}, {"name": "Thomas"}]}},
        {"node": {"name": {"nameText": {"text": "Extra"}, "primaryImage": None}, "characters": None}},
        {"node": {"name": None}},
    ]},
    "primaryVideos": _edge(id="vi1032782617"),
}


def test_imdb_movie_carries_the_full_record(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_title", lambda i: IMDB_RICH)
    info = ImdbProvider().movie("tt0133093")

    assert (info.rating, info.votes, info.mpaa, info.tagline) == (8.7, 2282440, "R", "Free your mind")
    assert info.tags == ["simulated reality", "hacker"] and info.countries == ["United States"] and info.studios == ["Warner Bros."]
    assert info.directors == ["Lana"] and info.writers == ["Lilly"]
    assert info.actors == [Person("Keanu", "Neo, Thomas", "https://img/k.jpg"), Person("Extra", None, None)]  # null names are skipped
    assert info.trailer_url == "https://www.imdb.com/video/vi1032782617/"
    assert info.fanart_url is None  # IMDb has no backdrop


def test_imdb_movie_tolerates_missing_sections(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_title", lambda i: {"id": "tt1", "titleText": {"text": "Bare"}})
    info = ImdbProvider().movie("tt1")
    assert (info.title, info.tags, info.actors, info.trailer_url, info.mpaa, info.votes) == ("Bare", [], [], None, None, None)


def test_imdb_episode_takes_crew_and_cast_from_its_own_record(monkeypatch):
    listed = {"id": "tt0959621", "titleText": {"text": "Pilot"}, "releaseDate": {"year": 2008, "month": 1, "day": 20}}
    detail = {"id": "tt0959621", "titleText": {"text": "Pilot"}, "ratingsSummary": {"aggregateRating": 9.1, "voteCount": 86171},
              "directors": _edge(name={"nameText": {"text": "Vince"}}), "cast": _edge(name={"nameText": {"text": "Bryan"}}, characters=[{"name": "Walter"}]),
              "runtime": {"seconds": 3480}}
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_episode", lambda s, se, ep: (listed, "Breaking Bad"))
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_title", lambda i: detail if i == "tt0959621" else None)

    info = ImdbProvider().episode("tt0903747", 1, 1)

    assert (info.rating, info.votes, info.directors, info.runtime, info.aired) == (9.1, 86171, ["Vince"], 58, "2008-01-20")
    assert info.actors == [Person("Bryan", "Walter", None)]


def test_imdb_series_record_and_status(monkeypatch):
    monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.get_title", lambda i: IMDB_RICH)
    for ongoing, expected in ((True, "Continuing"), (False, "Ended"), (None, None)):
        monkeypatch.setattr("VibraVid.services._base.metadata.imdb.imdb_client.is_ongoing", lambda i, value=ongoing: value)
        info = ImdbProvider().series("tt0944947")
        assert info.status == expected and info.mpaa == "R" and info.studios == ["Warner Bros."] and info.votes == 2282440


# ── TVDB ──────────────────────────────────────────────────────────────────

TVDB_DETAIL = {
    "name": "The Irishman", "year": "2019", "runtime": 209, "image": "/banners/p.jpg", "originalCountry": "usa",
    "genres": [{"name": "Crime"}], "status": {"name": "Released"},
    "companies": {"studio": [{"name": "Netflix"}], "production": [{"name": "Tribeca"}, {"name": "Netflix"}], "distributor": [{"name": "Nope"}]},
    "contentRatings": [{"country": "arg", "name": "SAM16"}, {"country": "usa", "name": "R"}, {"country": "ita", "name": "T"}],
    "trailers": [{"url": "https://www.youtube.com/watch?v=abc"}],
    "characters": [
        {"personName": "Al", "name": "Jimmy", "peopleType": "Actor", "sort": 2, "personImgURL": "https://i/al.jpg"},
        {"personName": "Robert", "name": "Frank", "peopleType": "Actor", "sort": 1},
        {"personName": "Martin", "peopleType": "Director"},
        {"personName": "Steve", "peopleType": "Writer"},
        {"personName": "Producer", "peopleType": "Producer"},
    ],
    "artworks": [{"type": 14, "image": "/a/low.jpg", "score": 1}, {"type": 14, "image": "/a/high.jpg", "score": 9}, {"type": 15, "image": "/a/bg.jpg", "score": 5}],
    "remoteIds": [{"sourceName": "IMDB", "id": "tt1302006"}],
}


def _tvdb(monkeypatch, detail):
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.extended", lambda i, mt, full=False: detail if full else None)
    monkeypatch.setattr("VibraVid.services._base.metadata.tvdb.tvdb_client.translation", lambda kind, i, lang="ita": {})


def test_tvdb_movie_carries_the_full_record(monkeypatch):
    _tvdb(monkeypatch, TVDB_DETAIL)
    info = TvdbProvider().movie("14156")

    assert info.mpaa == "T"  # Italy before the US
    assert info.studios == ["Netflix", "Tribeca"]  # studios + production, deduplicated, distributors left out
    assert info.countries == ["USA"] and info.trailer_url == "https://www.youtube.com/watch?v=abc"
    assert info.directors == ["Martin"] and info.writers == ["Steve"]
    assert [(a.name, a.role) for a in info.actors] == [("Robert", "Frank"), ("Al", "Jimmy")]  # by TVDB's sort order
    assert info.actors[1].image_url == "https://i/al.jpg"
    assert info.image_url == "https://artworks.thetvdb.com/banners/p.jpg"
    assert info.fanart_url == "https://artworks.thetvdb.com/a/bg.jpg"
    assert info.rating is None and info.votes is None


def test_tvdb_movie_poster_falls_back_to_the_best_scored_artwork(monkeypatch):
    _tvdb(monkeypatch, {**TVDB_DETAIL, "image": None})
    assert TvdbProvider().movie("1").image_url == "https://artworks.thetvdb.com/a/high.jpg"


def test_tvdb_series_record(monkeypatch):
    detail = {
        "name": "Death in Paradise", "year": "2011", "firstAired": "2011-10-25", "status": {"name": "Continuing"}, "averageRuntime": 58,
        "image": "https://i/s.jpg", "originalNetwork": {"name": "BBC One"}, "genres": [{"name": "Crime"}],
        "contentRatings": [{"country": "usa", "name": "TV-14"}], "remoteIds": [{"sourceName": "IMDB", "id": "tt1888075"}, {"sourceName": "TheMovieDB.com", "id": "41956"}],
        "characters": [{"personName": "Ben", "name": "DI Poole", "peopleType": "Actor", "sort": 1}],
        "artworks": [{"type": 3, "image": "/bg.jpg", "score": 3}],
    }
    _tvdb(monkeypatch, detail)
    info = TvdbProvider().series("252800")

    assert (info.status, info.runtime, info.premiered, info.mpaa, info.studios) == ("Continuing", 58, "2011-10-25", "TV-14", ["BBC One"])
    assert info.ids == {"tvdb": "252800", "imdb": "tt1888075", "tmdb": "41956"}
    assert info.fanart_url == "https://artworks.thetvdb.com/bg.jpg" and info.season_posters == {}


def test_tvdb_series_status_ended(monkeypatch):
    _tvdb(monkeypatch, {"name": "Old", "status": {"name": "Ended"}})
    assert TvdbProvider().series("1").status == "Ended"
    _tvdb(monkeypatch, {"name": "Old"})
    assert TvdbProvider().series("1").status is None


def test_tvdb_series_unknown_id_is_none(monkeypatch):
    _tvdb(monkeypatch, None)
    assert TvdbProvider().series("1") is None
