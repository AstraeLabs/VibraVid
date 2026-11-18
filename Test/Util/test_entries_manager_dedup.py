# 09.10.26

import logging

import pytest

from VibraVid.services._base import object as obj
from VibraVid.services._base.object import Entries, EntriesManager


@pytest.fixture(autouse=True)
def no_tmdb(monkeypatch):
    monkeypatch.setattr(type(obj.tmdb_client), "api_key", property(lambda self: None))


def _tv(id, year, name="La casa nella prateria", **kw):
    return Entries(id=id, name=name, type="tv", year=year, slug="la-casa-nella-prateria", **kw)


def test_same_name_different_id_and_year_are_both_kept():
    m = EntriesManager()
    m.add(_tv(65121, "2026"))
    m.add(_tv(8168, "1974"))
    assert len(m) == 2


def test_same_name_different_id_is_kept_even_with_unknown_year():
    m = EntriesManager()
    m.add(_tv(65121, "9999"))
    m.add(_tv(8168, "9999"))
    assert len(m) == 2


def test_same_name_same_id_is_duplicate():
    m = EntriesManager()
    m.add(_tv(8168, "1974"))
    m.add(_tv(8168, "1974"))
    assert len(m) == 1


def test_same_name_different_year_without_ids_is_kept():
    m = EntriesManager()
    m.add(_tv(None, "2026"))
    m.add(_tv(None, "1974"))
    assert len(m) == 2


def test_same_name_without_ids_and_unknown_year_is_duplicate():
    m = EntriesManager()
    m.add(_tv(None, "9999"))
    m.add(_tv(None, "9999"))
    assert len(m) == 1


def test_name_and_type_comparison_is_case_insensitive():
    m = EntriesManager()
    m.add(_tv(1, "2000", name="Foo"))
    m.add(_tv(1, "2000", name=" foo "))
    assert len(m) == 1


def test_different_type_is_kept():
    m = EntriesManager()
    m.add(Entries(id=1, name="Foo", type="tv", year="2000", slug="foo"))
    m.add(Entries(id=1, name="Foo", type="film", year="2000", slug="foo"))
    assert len(m) == 2


def test_music_same_track_is_duplicate_when_ids_equal_or_missing():
    m = EntriesManager()
    m.add(Entries(id="a", name="Song", type="track", artist="X", album="Y", year="2020", slug="s"))
    m.add(Entries(id="a", name="Song", type="track", artist="X", album="Y", year="2020", slug="s"))
    m.add(Entries(name="Song", type="track", artist="X", album="Y", year="2020", slug="s"))
    assert len(m) == 1


def test_music_different_artist_or_album_is_kept():
    m = EntriesManager()
    m.add(Entries(id="a", name="Song", type="track", artist="X", album="Y", year="2020", slug="s"))
    m.add(Entries(id="a", name="Song", type="track", artist="Z", album="Y", year="2020", slug="s"))
    m.add(Entries(id="a", name="Song", type="track", artist="X", album="W", year="2020", slug="s"))
    assert len(m) == 3


def test_music_same_track_with_different_ids_is_kept():
    m = EntriesManager()
    m.add(Entries(id="a", name="Song", type="track", artist="X", album="Y", year="2020", slug="s"))
    m.add(Entries(id="b", name="Song", type="track", artist="X", album="Y", year="2020", slug="s"))
    assert len(m) == 2


def test_duplicate_is_logged(caplog):
    m = EntriesManager()
    m.add(_tv(8168, "1974"))
    with caplog.at_level(logging.INFO, logger=obj.logger.name):
        m.add(_tv(8168, "1974"))
    assert "Skipping duplicate entry" in caplog.text
