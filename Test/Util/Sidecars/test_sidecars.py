# 01.10.26
# ruff: noqa: E402

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import metadata, site_search_manager, tmdb_artwork
from VibraVid.services._base.sidecars import Sidecars

JPEG = bytes([0xFF, 0xD8, 0xFF]) + b"jpeg-data"
PNG = bytes([0x89]) + b"PNG" + b"png-data"
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 "

MOVIE = {
    "title": "The Irishman",
    "original_title": "The Irishman",
    "release_date": "2019-11-01",
    "overview": "Frank & Jimmy <1975>",
    "tagline": "Una storia",
    "runtime": 209,
    "genres": [{"name": "Crime"}, {"name": "Dramma"}],
    "poster_path": "/poster.jpg",
    "imdb_id": "tt1302006",
}
SHOW = {"name": "Breaking Bad", "original_name": "Breaking Bad", "first_air_date": "2008-01-20"}
EPISODE = {
    "id": 62085,
    "name": "Pilot",
    "overview": "Un professore.",
    "air_date": "2008-01-20",
    "runtime": 59,
    "still_path": "/still.jpg",
}


class _FakeTmdb:
    """Stands in for tmdb_client._make_request: answers by endpoint, 404-style {} otherwise."""

    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, endpoint, params=None, retries=3):
        self.calls.append(endpoint)
        return dict(self.responses.get(endpoint, {}))


class _FakeClient:
    def __init__(self, content: bytes = JPEG, status: int = 200, error: Exception | None = None):
        self.content, self.status, self.error = content, status, error
        self.requested_headers: list[dict] = []

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, headers=None):
        self.requested_headers.append(headers or {})
        if self.error:
            raise self.error
        return SimpleNamespace(status_code=self.status, content=self.content)


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "The Irishman (2019).mkv"
    path.write_bytes(b"x")
    return str(path)


@pytest.fixture
def tmdb(monkeypatch):
    fake = _FakeTmdb({
        "movie/398978": MOVIE,
        "tv/1396": SHOW,
        "tv/1396/season/1/episode/1": EPISODE,
    })
    monkeypatch.setattr(tmdb_client, "_make_request", fake)
    monkeypatch.setattr(tmdb_client, "_api_key", "test-key")
    monkeypatch.setattr(tmdb_client, "resolve_actual_season_episode", lambda tid, s, e: (s, e))
    return fake


@pytest.fixture
def images(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr("VibraVid.services._base.sidecars.create_client", client)
    return client


@pytest.fixture(autouse=True)
def clean_context():
    context_tracker.sidecar_provider = context_tracker.sidecar_id = context_tracker.sidecar_media_type = None
    context_tracker.sidecar_verify = False
    context_tracker.episode_name = None
    context_tracker.season = context_tracker.episode = 0
    yield


def _target(provider, media_type, item_id):
    context_tracker.sidecar_provider = provider
    context_tracker.sidecar_media_type = media_type
    context_tracker.sidecar_id = str(item_id)



# ── writer ────────────────────────────────────────────────────────────────

def test_movie_writes_nfo_and_poster(video, tmdb, images):
    written = Sidecars(video, "tmdb", "movie", "398978").write()

    base = video[: -len(".mkv")]
    assert written == [base + ".nfo", base + "-poster.jpg"]
    root = ET.parse(base + ".nfo").getroot()
    assert root.tag == "movie"
    assert root.findtext("title") == "The Irishman"
    assert root.findtext("year") == "2019"
    assert root.findtext("plot") == "Frank & Jimmy <1975>"  # XML-escaped on disk, round-trips intact
    assert [g.text for g in root.findall("genre")] == ["Crime", "Dramma"]
    uniqueids = {u.get("type"): u.text for u in root.findall("uniqueid")}
    assert uniqueids == {"tmdb": "398978", "imdb": "tt1302006"}
    assert root.find("uniqueid[@type='tmdb']").get("default") == "true"
    assert root.findtext("tmdbid") == "398978"
    assert open(base + "-poster.jpg", "rb").read() == JPEG


def test_nfo_is_utf8_with_declaration(video, tmdb, images):
    tmdb.responses["movie/398978"] = {**MOVIE, "title": "Città & Più"}
    Sidecars(video, "tmdb", "movie", "398978").write()
    raw = open(video[: -len(".mkv")] + ".nfo", "rb").read()
    assert raw.startswith(b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    assert "Città &amp; Più".encode() in raw


def test_episode_writes_nfo_and_thumb(tmp_path, tmdb, images):
    video = tmp_path / "Breaking Bad S01E01.mkv"
    video.write_bytes(b"x")

    written = Sidecars(str(video), "tmdb", "tv", "1396", 1, 1).write()

    base = str(video)[: -len(".mkv")]
    assert written == [base + ".nfo", base + "-thumb.jpg"]
    root = ET.parse(base + ".nfo").getroot()
    assert root.tag == "episodedetails"
    assert (root.findtext("title"), root.findtext("showtitle")) == ("Pilot", "Breaking Bad")
    assert (root.findtext("season"), root.findtext("episode")) == ("1", "1")
    assert root.findtext("aired") == "2008-01-20"
    assert root.find("uniqueid[@type='tmdb']").text == "62085"  # the episode's own id


def test_episode_uses_tmdb_remapped_numbering(tmp_path, tmdb, images, monkeypatch):
    """Anime: the provider's absolute episode number maps to a real TMDB season/episode."""
    monkeypatch.setattr(tmdb_client, "resolve_actual_season_episode", lambda tid, s, e: (21, 1000))
    tmdb.responses["tv/1396/season/21/episode/1000"] = {**EPISODE, "name": "Ep 1000"}
    video = tmp_path / "ep.mkv"
    video.write_bytes(b"x")

    Sidecars(str(video), "tmdb", "tv", "1396", 1, 1000).write()

    root = ET.parse(str(tmp_path / "ep.nfo")).getroot()
    assert (root.findtext("season"), root.findtext("episode"), root.findtext("title")) == ("21", "1000", "Ep 1000")


def test_images_ask_for_jpeg_not_webp(video, tmdb, images):
    Sidecars(video, "tmdb", "movie", "398978").write()
    assert images.requested_headers[0]["Accept"].startswith("image/jpeg")


def test_webp_image_is_skipped_but_nfo_is_written(video, tmdb, images):
    images.content = WEBP
    written = Sidecars(video, "tmdb", "movie", "398978").write()
    assert [Path(p).suffix for p in written] == [".nfo"]


def test_png_image_gets_png_extension(video, tmdb, images):
    images.content = PNG
    written = Sidecars(video, "tmdb", "movie", "398978").write()
    assert written[1].endswith("-poster.png")


@pytest.mark.parametrize("failure", [{"status": 404}, {"content": b""}, {"error": OSError("down")}])
def test_image_failures_never_lose_the_nfo(video, tmdb, images, failure):
    for key, value in failure.items():
        setattr(images, {"status": "status", "content": "content", "error": "error"}[key], value)
    written = Sidecars(video, "tmdb", "movie", "398978").write()
    assert [Path(p).suffix for p in written] == [".nfo"]


def test_nothing_is_written_without_tmdb_data(video, tmdb, images):
    assert Sidecars(video, "tmdb", "movie", "111").write() == []  # unknown id -> empty answer
    assert not list(Path(video).parent.glob("*.nfo"))


def test_nothing_is_written_for_a_missing_video(tmp_path, tmdb, images):
    assert Sidecars(str(tmp_path / "ghost.mkv"), "tmdb", "movie", "398978").write() == []


def test_nothing_is_written_without_api_key(video, tmdb, images, monkeypatch):
    monkeypatch.setattr(tmdb_client, "_api_key", "")
    with patch("VibraVid.provider.tmdb._configured_api_key", return_value=""):
        assert Sidecars(video, "tmdb", "movie", "398978").write() == []


@pytest.mark.parametrize("season,episode", [(0, 1), (1, 0), (0, 0)])
def test_episode_needs_season_and_episode(tmp_path, tmdb, images, season, episode):
    video = tmp_path / "x.mkv"
    video.write_bytes(b"x")
    assert Sidecars(str(video), "tmdb", "tv", "1396", season, episode).write() == []


def test_unexpected_errors_are_swallowed(video, tmdb, images, monkeypatch):
    monkeypatch.setattr(tmdb_client, "_make_request", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert Sidecars(video, "tmdb", "movie", "398978").write() == []


def test_rewriting_overwrites_and_leaves_no_temp_files(video, tmdb, images):
    Sidecars(video, "tmdb", "movie", "398978").write()
    tmdb.responses["movie/398978"] = {**MOVIE, "title": "Updated"}
    Sidecars(video, "tmdb", "movie", "398978").write()

    assert ET.parse(video[: -len(".mkv")] + ".nfo").getroot().findtext("title") == "Updated"
    assert not list(Path(video).parent.glob("*.tmp"))


# ── snapshot / write_for ──────────────────────────────────────────────────

def test_snapshot_is_none_without_a_target():
    assert Sidecars.snapshot() is None


def test_snapshot_needs_all_three_fields():
    context_tracker.sidecar_provider, context_tracker.sidecar_media_type = "tmdb", "movie"  # no id
    assert Sidecars.snapshot() is None


def test_snapshot_of_a_film():
    _target("tmdb", "movie", 398978)
    assert Sidecars.snapshot() == Sidecars.Target("tmdb", "movie", "398978")


def test_snapshot_keeps_provider_season_and_episode():
    _target("tvdb", "tv", 81189)
    context_tracker.season, context_tracker.episode = 2, 5
    context_tracker.episode_name = "Pilot"
    context_tracker.sidecar_verify = True
    assert Sidecars.snapshot() == Sidecars.Target("tvdb", "tv", "81189", 2, 5, "Pilot", True)


def test_verify_flag_and_episode_name_follow_the_context():
    _target("tmdb", "tv", 41956)
    context_tracker.season, context_tracker.episode = 15, 1
    context_tracker.episode_name = "Corruzione a Saint Marie"
    context_tracker.sidecar_verify = True
    assert Sidecars.snapshot() == Sidecars.Target("tmdb", "tv", "41956", 15, 1, "Corruzione a Saint Marie", True)


def test_empty_episode_name_becomes_none():
    _target("tmdb", "tv", 41956)
    context_tracker.episode_name = ""
    assert Sidecars.snapshot().episode_name is None


def _verified(tmp_path, tmdb, images, episode_name, verify=True):
    video = tmp_path / "Pilot S01E01.mkv"
    video.write_bytes(b"x")
    return Sidecars.write_for(str(video), Sidecars.Target("tmdb", "tv", "1396", 1, 1, episode_name, verify))


def test_candidate_episode_is_written_when_the_title_matches(tmp_path, tmdb, images):
    assert len(_verified(tmp_path, tmdb, images, "Pilot")) == 2


def test_candidate_episode_title_comparison_is_tolerant(tmp_path, tmdb, images):
    assert len(_verified(tmp_path, tmdb, images, "PILOT!")) == 2


def test_candidate_episode_is_skipped_when_the_title_differs(tmp_path, tmdb, images):
    assert _verified(tmp_path, tmdb, images, "Un altro episodio") == []
    assert not list(tmp_path.glob("*.nfo")) and not list(tmp_path.glob("*.jpg"))


@pytest.mark.parametrize("name", [None, ""])
def test_candidate_episode_without_a_site_title_is_skipped(tmp_path, tmdb, images, name):
    assert _verified(tmp_path, tmdb, images, name) == []


def test_reliable_match_does_not_need_the_episode_title(tmp_path, tmdb, images):
    assert len(_verified(tmp_path, tmdb, images, None, verify=False)) == 2


def test_verification_does_not_apply_to_films(video, tmdb, images):
    assert len(Sidecars.write_for(video, Sidecars.Target("tmdb", "movie", "398978", 0, 0, None, True))) == 2


def test_write_for_without_snapshot_is_a_noop(video):
    assert Sidecars.write_for(video, None) == []


def test_write_for_writes_from_the_snapshot(video, tmdb, images):
    assert len(Sidecars.write_for(video, Sidecars.Target("tmdb", "movie", "398978"))) == 2


def test_unknown_or_unavailable_provider_writes_nothing(video, tmdb, images):
    assert Sidecars(video, "nope", "movie", "1").write() == []
    with patch("VibraVid.services._base.metadata.tvdb.tvdb_client") as client:
        client.api_key = None
        assert Sidecars(video, "tvdb", "movie", "1").write() == []



# ── trust rule ────────────────────────────────────────────────────────────

def _details(monkeypatch, media_type="movie", **fields):
    base = {"movie": MOVIE, "tv": SHOW}[media_type]
    monkeypatch.setattr(tmdb_client, "_make_request", lambda *a, **k: {**base, **fields})


def test_site_supplied_id_is_trusted_without_any_check(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_make_request", lambda *a, **k: pytest.fail("no TMDB call expected"))
    assert tmdb_artwork.is_trusted_match("movie", 5, site_tmdb_id=5)


def test_no_id_is_never_trusted():
    assert not tmdb_artwork.is_trusted_match("movie", None, site_tmdb_id=5)


@pytest.mark.parametrize("year,expected", [(2019, True), (2020, True), (2018, True), (2021, False), (2016, False), (None, False)])
def test_movie_year_tolerance(monkeypatch, year, expected):
    _details(monkeypatch)
    assert tmdb_artwork.is_trusted_match("movie", 398978, name="The Irishman", year=year) is expected


def test_year_as_string_or_range(monkeypatch):
    _details(monkeypatch)
    assert tmdb_artwork.is_trusted_match("movie", 398978, name="The Irishman", year="2019")
    assert tmdb_artwork.is_trusted_match("movie", 398978, name="The Irishman", year="2019-2020")


def test_title_must_be_similar(monkeypatch):
    _details(monkeypatch)
    assert not tmdb_artwork.is_trusted_match("movie", 398978, name="Inception", year=2019)


def test_original_title_also_counts(monkeypatch):
    _details(monkeypatch, title="Il Padrino", original_title="The Godfather", release_date="1972-03-14")
    assert tmdb_artwork.is_trusted_match("movie", 238, name="The Godfather", year=1972)


def test_series_use_first_air_date_and_name(monkeypatch):
    _details(monkeypatch, "tv")
    assert tmdb_artwork.is_trusted_match("tv", 1396, name="Breaking Bad", year=2009)
    assert not tmdb_artwork.is_trusted_match("tv", 1396, name="Breaking Bad", year=2012)


def test_unknown_tmdb_entry_is_not_trusted(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_make_request", lambda *a, **k: {})
    assert not tmdb_artwork.is_trusted_match("movie", 1, name="The Irishman", year=2019)


def test_missing_release_date_is_not_trusted(monkeypatch):
    _details(monkeypatch, release_date="")
    assert not tmdb_artwork.is_trusted_match("movie", 398978, name="The Irishman", year=2019)


# ── near-year resolution ──────────────────────────────────────────────────

def test_near_year_tries_exact_then_previous_then_next(monkeypatch):
    seen = []

    def fake(slug, year, media_type, *a, **k):
        seen.append(year)
        return {"type": media_type, "id": 7} if year == "2021" else None

    monkeypatch.setattr(tmdb_client, "get_type_and_id_by_slug_year", fake)
    monkeypatch.setattr(tmdb_client, "_api_key", "k")

    assert tmdb_artwork.resolve_tmdb_id_near_year("movie", None, name="Dune", year=2020) == 7
    assert seen == ["2020", "2019", "2021"]


def test_near_year_stops_at_the_first_hit(monkeypatch):
    seen = []
    monkeypatch.setattr(tmdb_client, "get_type_and_id_by_slug_year", lambda s, y, m, *a, **k: seen.append(y) or {"type": m, "id": 1})
    monkeypatch.setattr(tmdb_client, "_api_key", "k")
    assert tmdb_artwork.resolve_tmdb_id_near_year("movie", None, name="Dune", year=2021) == 1
    assert seen == ["2021"]


def test_near_year_normalises_a_year_range(monkeypatch):
    seen = []
    monkeypatch.setattr(tmdb_client, "get_type_and_id_by_slug_year", lambda s, y, m, *a, **k: seen.append(y) and None)
    monkeypatch.setattr(tmdb_client, "_api_key", "k")
    tmdb_artwork.resolve_tmdb_id_near_year("movie", None, name="Dune", year="2011-2019")
    assert seen[0] == "2011"  # not "2011-2019", which the plain search cannot parse


def test_near_year_returns_the_site_id_untouched(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_api_key", "k")
    monkeypatch.setattr(tmdb_client, "get_type_and_id_by_slug_year", lambda *a, **k: pytest.fail("no search expected"))
    assert tmdb_artwork.resolve_tmdb_id_near_year("movie", 398978, name="x", year=None) == 398978


def test_near_year_without_a_year_searches_once(monkeypatch):
    seen = []
    monkeypatch.setattr(tmdb_client, "get_type_and_id_by_slug_year", lambda s, y, m, *a, **k: seen.append(y) or {"type": m, "id": 9})
    monkeypatch.setattr(tmdb_client, "_api_key", "k")
    assert tmdb_artwork.resolve_tmdb_id_near_year("movie", None, name="Dune", year=None) == 9
    assert seen == [None]


def test_near_year_for_series_respects_the_site_whitelist(monkeypatch):
    monkeypatch.setattr(tmdb_client, "_api_key", "k")
    monkeypatch.setattr(tmdb_client, "get_type_and_id_by_slug_year", lambda s, y, m, *a, **k: {"type": m, "id": 1396})

    with patch.object(context_tracker.local, "site_name", "streamingcommunity", create=True):
        assert tmdb_artwork.resolve_tmdb_id_near_year("tv", None, name="Breaking Bad", year=2008) == 1396
    with patch.object(context_tracker.local, "site_name", "sito_sconosciuto", create=True):
        assert tmdb_artwork.resolve_tmdb_id_near_year("tv", None, name="Breaking Bad", year=2008) is None
        assert tmdb_artwork.resolve_tmdb_id_near_year("tv", 1396, name="Breaking Bad", year=2008) == 1396


# ── wiring into the download context ──────────────────────────────────────

def _title(**fields):
    return SimpleNamespace(**{"name": "The Irishman", "slug": "the-irishman", "year": 2019, "tmdb_id": None, **fields})


def test_flag_defaults_to_off():
    with patch.object(tmdb_artwork.config_manager.config, "get_bool", side_effect=lambda s, k, default=False: default):
        assert tmdb_artwork.sidecars_enabled() is False


def test_flag_off_touches_nothing_and_asks_nobody(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "sidecars_enabled", lambda: False)
    monkeypatch.setattr(metadata, "find_sidecar_target", lambda *a, **k: pytest.fail("no lookup expected"))

    site_search_manager._set_sidecar_target("movie", _title())

    assert (context_tracker.sidecar_provider, context_tracker.sidecar_id) == (None, None)
    assert Sidecars.snapshot() is None


def test_a_title_only_candidate_sets_the_verify_flag(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "sidecars_enabled", lambda: True)
    monkeypatch.setattr(metadata, "find_sidecar_target", lambda media_type, title: ("tmdb", "41956", True))

    site_search_manager._set_sidecar_target("tv", _title(name="Delitti in Paradiso", year=None))

    assert (context_tracker.sidecar_provider, context_tracker.sidecar_id, context_tracker.sidecar_verify) == ("tmdb", "41956", True)


def test_a_reliable_match_clears_a_previous_verify_flag(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "sidecars_enabled", lambda: True)
    context_tracker.sidecar_verify = True
    monkeypatch.setattr(metadata, "find_sidecar_target", lambda media_type, title: ("tmdb", "398978", False))

    site_search_manager._set_sidecar_target("movie", _title())

    assert context_tracker.sidecar_verify is False


def test_a_reliable_match_is_recorded(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "sidecars_enabled", lambda: True)
    monkeypatch.setattr(metadata, "find_sidecar_target", lambda media_type, title: ("imdb", "tt1302006", False))

    site_search_manager._set_sidecar_target("movie", _title())

    assert (context_tracker.sidecar_provider, context_tracker.sidecar_id, context_tracker.sidecar_media_type) == ("imdb", "tt1302006", "movie")


def test_no_reliable_match_leaves_the_context_clean(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "sidecars_enabled", lambda: True)
    monkeypatch.setattr(metadata, "find_sidecar_target", lambda *a, **k: None)

    site_search_manager._set_sidecar_target("movie", _title())

    assert (context_tracker.sidecar_provider, context_tracker.sidecar_id) == (None, None)


def test_a_new_title_resets_the_previous_one(monkeypatch):
    monkeypatch.setattr(tmdb_artwork, "sidecars_enabled", lambda: True)
    monkeypatch.setattr(metadata, "find_sidecar_target", lambda media_type, title: ("tmdb", "398978", False))
    site_search_manager._set_sidecar_target("movie", _title())
    assert context_tracker.sidecar_provider == "tmdb"

    monkeypatch.setattr(metadata, "find_sidecar_target", lambda *a, **k: None)
    site_search_manager._set_sidecar_target("tv", _title(name="Altro", year=2000))

    assert (context_tracker.sidecar_provider, context_tracker.sidecar_id, context_tracker.sidecar_media_type) == (None, None, "tv")



# ── hook in the real downloaders ──────────────────────────────────────────

def _finalize(tmp_path, monkeypatch):
    """Run the real BaseDownloader.__init__ + _finalize on a finished file, with everything else stubbed."""
    from VibraVid.core.downloader import base

    out = tmp_path / f"The Irishman (2019).{base.EXTENSION_OUTPUT}"  # BaseDownloader enforces the configured extension
    out.write_bytes(b"x")
    dl = base.BaseDownloader(str(out), "_hls_temp", sanitize_path=False)
    monkeypatch.setattr(dl, "_move_to_final_location", lambda f: None)
    monkeypatch.setattr(dl, "_verify_output", lambda: True)
    monkeypatch.setattr(base, "get_media_metadata", lambda p: {"height": 0})
    monkeypatch.setattr(base, "execute_hooks", lambda *a, **k: None)
    monkeypatch.setattr(base.download_tracker, "complete_download", lambda *a, **k: None)
    monkeypatch.setattr("VibraVid.utils.vault.vault_1.claudio_vault.track_download_async", lambda **k: None)
    dl._finalize(final_file=str(out))
    return out


def test_downloader_writes_sidecars_when_the_match_is_trusted(tmp_path, tmdb, images, monkeypatch):
    _target("tmdb", "movie", 398978)
    out = _finalize(tmp_path, monkeypatch)
    assert out.with_suffix(".nfo").exists()
    assert (tmp_path / "The Irishman (2019)-poster.jpg").exists()


def test_downloader_writes_nothing_by_default(tmp_path, tmdb, images, monkeypatch):
    _finalize(tmp_path, monkeypatch)
    assert not list(tmp_path.glob("*.nfo")) and not list(tmp_path.glob("*.jpg"))


def test_downloader_skips_sidecars_for_a_failed_download(tmp_path, tmdb, images, monkeypatch):
    from VibraVid.core.downloader import base

    _target("tmdb", "movie", 398978)
    out = tmp_path / f"The Irishman (2019).{base.EXTENSION_OUTPUT}"  # BaseDownloader enforces the configured extension
    out.write_bytes(b"x")
    dl = base.BaseDownloader(str(out), "_hls_temp", sanitize_path=False)
    monkeypatch.setattr(dl, "_move_to_final_location", lambda f: None)
    monkeypatch.setattr(dl, "_verify_output", lambda: False)
    monkeypatch.setattr(base, "execute_hooks", lambda *a, **k: None)
    monkeypatch.setattr(base.download_tracker, "complete_download", lambda *a, **k: None)
    monkeypatch.setattr("VibraVid.utils.vault.vault_1.claudio_vault.track_download_async", lambda **k: None)

    dl._finalize(final_file=str(out))

    assert not list(tmp_path.glob("*.nfo"))


def test_downloader_uses_the_context_captured_at_creation(tmp_path, tmdb, images, monkeypatch):
    """The context moves on to the next item while this one downloads: the snapshot must not."""
    from VibraVid.core.downloader import base

    _target("tmdb", "movie", 398978)
    out = tmp_path / f"The Irishman (2019).{base.EXTENSION_OUTPUT}"  # BaseDownloader enforces the configured extension
    out.write_bytes(b"x")
    dl = base.BaseDownloader(str(out), "_hls_temp", sanitize_path=False)

    context_tracker.sidecar_provider = None  # next title, no reliable match
    context_tracker.sidecar_id = None

    monkeypatch.setattr(dl, "_move_to_final_location", lambda f: None)
    monkeypatch.setattr(dl, "_verify_output", lambda: True)
    monkeypatch.setattr(base, "get_media_metadata", lambda p: {"height": 0})
    monkeypatch.setattr(base, "execute_hooks", lambda *a, **k: None)
    monkeypatch.setattr(base.download_tracker, "complete_download", lambda *a, **k: None)
    monkeypatch.setattr("VibraVid.utils.vault.vault_1.claudio_vault.track_download_async", lambda **k: None)
    dl._finalize(final_file=str(out))

    assert out.with_suffix(".nfo").exists()
