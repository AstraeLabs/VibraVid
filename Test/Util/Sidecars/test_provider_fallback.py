# 25.10.26
# ruff: noqa: E402

import sys
from pathlib import Path
from types import SimpleNamespace

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.provider import tmdb as tmdb_module
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.services._base import sidecars as sidecars_module
from VibraVid.services._base.metadata import BaseMetadataProvider, EpisodeInfo
from VibraVid.services._base.sidecars import Sidecars


class FakeProvider(BaseMetadataProvider):
    """A provider with a fixed title-only candidate and episode titles; the show title carries its name so a test can tell who wrote a file."""

    def __init__(self, name, candidate, titles):
        self.NAME = name
        self.candidate, self.titles = candidate, titles
        self.lookups = 0

    def find_by_title(self, media_type, name):
        self.lookups += 1
        return self.candidate

    def episode(self, item_id, season, episode):
        title = self.titles.get(episode)
        return EpisodeInfo(title=title, show_title=self.NAME, season=season, episode=episode) if title else None

    def series(self, item_id):
        return None


@pytest.fixture
def providers(monkeypatch):
    """tvdb (first), tmdb, imdb; the doc series: tvdb calls episode 1 'Meno 12', the others 'Amnesia'."""
    table = {
        "tvdb": FakeProvider("tvdb", "10", {1: "Meno 12", 2: "Selfie", 3: "Niente di personale"}),
        "tmdb": FakeProvider("tmdb", "20", {1: "Amnesia", 2: "Selfie", 3: "Niente di personale"}),
        "imdb": FakeProvider("imdb", "tt30", {1: "Amnesia", 2: "Selfie"}),
    }
    monkeypatch.setattr(sidecars_module, "get_provider", table.get)
    monkeypatch.setattr(sidecars_module, "provider_names", lambda: list(table))
    Sidecars._confirmed_by.clear()
    Sidecars._alternatives.clear()
    yield table
    Sidecars._confirmed_by.clear()
    Sidecars._alternatives.clear()


def _write(tmp_path, episode, site_name, title="Doc - Nelle tue mani", verify=True, provider="tvdb", item="10"):
    video = tmp_path / f"Doc S01E{episode:02d}.mkv"
    video.write_bytes(b"x")
    written = Sidecars.write_for(str(video), Sidecars.Target(provider, "tv", item, 1, episode, site_name, verify, title))
    return [p for p in written if p.endswith(".nfo")]


def _written_by(nfo_paths):
    return Path(nfo_paths[0]).read_text(encoding="utf-8").split("<showtitle>")[1].split("</showtitle>")[0]


def test_next_provider_confirms_when_the_first_calls_the_episode_differently(tmp_path, providers):
    assert _written_by(_write(tmp_path, 1, "Amnesia")) == "tmdb"


def test_first_provider_is_kept_when_it_confirms(tmp_path, providers):
    assert _written_by(_write(tmp_path, 2, "Selfie")) == "tvdb"
    assert providers["tmdb"].lookups == 0  # the alternatives are looked up only when needed


def test_provider_without_the_episode_gives_way_to_the_next(tmp_path, providers):
    providers["tvdb"].titles = {}
    assert _written_by(_write(tmp_path, 2, "Selfie")) == "tmdb"


def test_series_stays_on_the_provider_that_confirmed_an_earlier_episode(tmp_path, providers):
    assert _written_by(_write(tmp_path, 1, "Amnesia")) == "tmdb"
    assert _written_by(_write(tmp_path, 2, "Selfie")) == "tmdb"  # tvdb would confirm it too


def test_primary_is_used_again_when_the_remembered_provider_fails(tmp_path, providers):
    assert _written_by(_write(tmp_path, 1, "Amnesia")) == "tmdb"
    providers["tmdb"].titles = {}
    assert _written_by(_write(tmp_path, 3, "Niente di personale")) == "tvdb"
    assert not Sidecars._confirmed_by


def test_alternatives_are_looked_up_once_per_series(tmp_path, providers):
    _write(tmp_path, 1, "Amnesia")
    Sidecars._confirmed_by.clear()
    _write(tmp_path, 1, "Amnesia")
    assert providers["tmdb"].lookups == 1


def test_nothing_is_written_when_no_provider_confirms(tmp_path, providers):
    assert _write(tmp_path, 1, "Un altro titolo") == []


def test_no_fallback_without_the_series_title(tmp_path, providers):
    assert _write(tmp_path, 1, "Amnesia", title=None) == []
    assert providers["tmdb"].lookups == 0


def test_no_fallback_for_a_reliable_match(tmp_path, providers):
    assert _write(tmp_path, 1, "Amnesia", verify=False) == _write(tmp_path, 1, None, verify=False)  # tvdb's own episode, no lookups
    assert providers["tmdb"].lookups == 0


def test_provider_without_a_key_is_never_asked(tmp_path, providers):
    providers["tmdb"].available = lambda: False
    assert _written_by(_write(tmp_path, 1, "Amnesia")) == "imdb"
    assert providers["tmdb"].lookups == 0


def test_alternative_without_a_candidate_is_skipped(tmp_path, providers):
    providers["tmdb"].candidate = None
    assert _written_by(_write(tmp_path, 1, "Amnesia")) == "imdb"


def test_generic_titles_confirm_across_providers(tmp_path, providers):
    providers["tvdb"].titles = {1: "Meno 12"}
    providers["imdb"].titles = {1: "Episode #1.1"}
    providers["tmdb"].candidate = None
    assert _written_by(_write(tmp_path, 1, "Episodio 1")) == "imdb"


# ── TMDB: a missing episode is not an error ───────────────────────────────

class _NotFound(Exception):
    def __init__(self):
        super().__init__("HTTP Error 404")
        self.response = SimpleNamespace(status_code=404)


class _Http:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.calls += 1
        return SimpleNamespace(raise_for_status=self._raise, json=lambda: {})

    def _raise(self):
        raise self.error


def test_tmdb_404_is_quiet_and_not_retried(monkeypatch):
    logged = []
    http = _Http(_NotFound())
    monkeypatch.setattr(tmdb_module, "create_client", http)
    monkeypatch.setattr(tmdb_module, "console", SimpleNamespace(log=lambda *a, **k: logged.append(a)))
    monkeypatch.setattr(tmdb_client, "_api_key", "test-key")
    monkeypatch.setattr(tmdb_client, "_cache", {})

    assert tmdb_client._make_request("tv/1/season/1/episode/9") == {}
    assert logged == [] and http.calls == 1
