# 01.10.26
# ruff: noqa: E402

import sys
from pathlib import Path
from unittest.mock import patch

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.provider import imdb as imdb_module
from VibraVid.provider import tvdb as tvdb_module
from VibraVid.provider.imdb import IMDbClient
from VibraVid.provider.tvdb import TVDBClient


class _Response:
    def __init__(self, body=None, status=200):
        self.body, self.status_code = body, status

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Http:
    """Scripted stand-in for create_client(): GET/POST pop queued responses, and every call is recorded."""

    def __init__(self, gets=(), posts=()):
        self.gets, self.posts = list(gets), list(posts)
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _next(self, queue, kind, url, kwargs):
        self.calls.append((kind, url, kwargs))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self._next(self.gets, "GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._next(self.posts, "POST", url, kwargs)


# ── IMDb client ───────────────────────────────────────────────────────────

def _imdb(monkeypatch, **queues):
    http = _Http(**queues)
    monkeypatch.setattr(imdb_module, "create_client", http)
    return IMDbClient(), http


def test_imdb_uses_the_persisted_query_get_first(monkeypatch):
    client, http = _imdb(monkeypatch, gets=[_Response({"data": {"title": {"id": "tt1"}}})])
    assert client.graphql("Op", "query Op { x }") == {"title": {"id": "tt1"}}
    assert [c[0] for c in http.calls] == ["GET"]
    params = http.calls[0][2]["params"]
    assert params["variables"] == "{}" and " " not in params["extensions"]  # the gateway reads "+" literally


def test_imdb_registers_an_unknown_persisted_query_with_one_post(monkeypatch):
    missing = {"errors": [{"extensions": {"code": "PERSISTED_QUERY_NOT_FOUND"}}]}
    client, http = _imdb(monkeypatch, gets=[_Response(missing)], posts=[_Response({"data": {"ok": 1}})])
    assert client.graphql("Op", "query Op { x }") == {"ok": 1}
    assert [c[0] for c in http.calls] == ["GET", "POST"]
    assert http.calls[1][2]["json"]["query"] == "query Op { x }"


def test_imdb_message_form_of_the_missing_query_error_also_triggers_the_post(monkeypatch):
    client, http = _imdb(monkeypatch, gets=[_Response({"errors": [{"message": "PersistedQueryNotFound"}]})], posts=[_Response({"data": {"ok": 1}})])
    assert client.graphql("Op", "query Op { y }") == {"ok": 1}


def test_imdb_errors_network_failures_and_bad_json_give_none(monkeypatch):
    for gets in ([_Response({"errors": [{"message": "Invalid query"}]})], [OSError("down")], [_Response(ValueError("not json"))]):
        client, _ = _imdb(monkeypatch, gets=gets)
        assert client.graphql("Op", "query Op { z }") is None


def test_imdb_caches_identical_queries(monkeypatch):
    client, http = _imdb(monkeypatch, gets=[_Response({"data": {"a": 1}})])
    assert client.graphql("Op", "query Op { q }") == client.graphql("Op", "query Op { q }") == {"a": 1}
    assert len(http.calls) == 1


def test_imdb_search_query_has_year_window_types_and_escaped_title(monkeypatch):
    client = IMDbClient()
    sent = {}
    client.graphql = lambda op, query: sent.update(query=query) or {"advancedTitleSearch": {"edges": [{"node": {"title": {"id": "tt1"}}}]}}

    assert client.search_titles('Say "Hi" \\ now', "movie", 2020) == [{"id": "tt1"}]

    query = sent["query"]
    assert 'searchTerm: "Say \\"Hi\\" \\\\ now"' in query
    assert 'start: "2019-01-01", end: "2021-12-31"' in query  # year +-1
    assert '["movie", "tvMovie"]' in query


def test_imdb_search_without_year_and_for_series():
    client = IMDbClient()
    sent = {}
    client.graphql = lambda op, query: sent.update(query=query) or None
    assert client.search_titles("Breaking Bad", "tv") == []
    assert "releaseDateConstraint" not in sent["query"] and '["tvSeries", "tvMiniSeries"]' in sent["query"]


def _episodes_payload(*pairs):
    edges = [
        {"node": {"id": f"tt{s}{e}", "titleText": {"text": f"S{s}E{e}"},
                  "series": {"displayableEpisodeNumber": {"displayableSeason": {"season": str(s)}, "episodeNumber": {"episodeNumber": str(e)}}}}}
        for s, e in pairs
    ]
    return {"title": {"titleText": {"text": "Show"}, "episodes": {"episodes": {"edges": edges}}}}


def test_imdb_episode_lookup_filters_the_season_and_matches_the_number():
    client = IMDbClient()
    sent = {}
    client.graphql = lambda op, query: sent.update(query=query) or _episodes_payload((2, 1), (2, 2), (2, 3))

    node, show = client.get_episode("tt0903747", 2, 2)

    assert (node["titleText"]["text"], show) == ("S2E2", "Show")
    assert 'includeSeasons: ["2"]' in sent["query"] and sent["query"].count("{") == sent["query"].count("}")


def test_imdb_episode_lookup_misses_cleanly():
    client = IMDbClient()
    client.graphql = lambda op, query: _episodes_payload((1, 1))
    assert client.get_episode("tt1", 1, 9) == (None, None)
    client.graphql = lambda op, query: None
    assert client.get_episode("tt1", 1, 1) == (None, None)


# ── TVDB client ───────────────────────────────────────────────────────────

def _tvdb(monkeypatch, key="K", **queues):
    http = _Http(**queues)
    monkeypatch.setattr(tvdb_module, "create_client", http)
    return TVDBClient(api_key=key), http


LOGIN_OK = _Response({"data": {"token": "T1"}})


def test_tvdb_key_comes_from_the_environment_first(monkeypatch):
    monkeypatch.setenv("TVDB_API_KEY", " env-key ")
    with patch.object(tvdb_module.config_manager.login, "get", return_value="login-key"):
        assert tvdb_module._configured_api_key() == "env-key"


def test_tvdb_key_falls_back_to_login_json(monkeypatch):
    monkeypatch.delenv("TVDB_API_KEY", raising=False)
    with patch.object(tvdb_module.config_manager.login, "get", return_value=" login-key ") as getter:
        assert tvdb_module._configured_api_key() == "login-key"
    assert getter.call_args.args[:2] == ("Provider", "tvdb")


def test_tvdb_without_any_key_is_unavailable(monkeypatch):
    monkeypatch.delenv("TVDB_API_KEY", raising=False)
    with patch.object(tvdb_module.config_manager.login, "get", return_value=""):
        assert tvdb_module._configured_api_key() is None
        assert TVDBClient().api_key is None


def test_tvdb_logs_in_once_and_sends_the_bearer_token(monkeypatch):
    client, http = _tvdb(monkeypatch, gets=[_Response({"data": [{"a": 1}]}), _Response({"data": [{"b": 2}]})], posts=[LOGIN_OK])
    assert client.api_get("/x") == [{"a": 1}] and client.api_get("/y") == [{"b": 2}]
    assert [c[0] for c in http.calls] == ["POST", "GET", "GET"]
    assert http.calls[0][2]["json"] == {"apikey": "K"}
    assert http.calls[1][2]["headers"] == {"Authorization": "Bearer T1"}


def test_tvdb_relogs_in_after_a_401_and_retries(monkeypatch):
    client, http = _tvdb(monkeypatch, gets=[_Response(status=401), _Response({"data": {"ok": 1}})], posts=[LOGIN_OK, _Response({"data": {"token": "T2"}})])
    assert client.api_get("/x") == {"ok": 1}
    assert http.calls[-1][2]["headers"]["Authorization"] == "Bearer T2"


def test_tvdb_failures_give_none_not_exceptions(monkeypatch):
    client, _ = _tvdb(monkeypatch, gets=[_Response(status=404)], posts=[LOGIN_OK])
    assert client.api_get("/missing") is None
    client, _ = _tvdb(monkeypatch, posts=[OSError("down")])
    assert client.api_get("/x") is None
    client, _ = _tvdb(monkeypatch, key=None)
    with patch.object(tvdb_module, "_configured_api_key", return_value=None):
        assert client.api_get("/x") is None


def test_tvdb_caches_identical_requests(monkeypatch):
    client, http = _tvdb(monkeypatch, gets=[_Response({"data": {"n": 1}})], posts=[LOGIN_OK])
    assert client.api_get("/x", {"p": 1}) == client.api_get("/x", {"p": 1})
    assert [c[0] for c in http.calls] == ["POST", "GET"]


def test_tvdb_remote_id_picks_the_wanted_kind(monkeypatch):
    hits = [{"movie": {"id": 4847, "name": "Mirror"}}, {"series": {"id": 81189, "name": "Breaking Bad"}}]
    client, _ = _tvdb(monkeypatch, gets=[_Response({"data": hits}), _Response({"data": hits})], posts=[LOGIN_OK])
    assert client.find_by_remote_id("tt0903747", "tv") == 81189
    assert client.find_by_remote_id("tt0903747x", "movie") == 4847


def test_tvdb_remote_id_without_that_kind_is_none(monkeypatch):
    client, _ = _tvdb(monkeypatch, gets=[_Response({"data": [{"movie": {"id": 1}}]})], posts=[LOGIN_OK])
    assert client.find_by_remote_id("tt1", "tv") is None


def test_tvdb_search_passes_type_and_limit(monkeypatch):
    client, http = _tvdb(monkeypatch, gets=[_Response({"data": []})], posts=[LOGIN_OK])
    assert client.search("Dune", "movie") == []
    assert http.calls[1][2]["params"] == {"query": "Dune", "limit": 10, "type": "movie"}


def test_tvdb_episodes_follow_pagination(monkeypatch):
    pages = [_Response({"data": {"episodes": [{"id": i} for i in range(500)]}}), _Response({"data": {"episodes": [{"id": 500}]}})]
    client, http = _tvdb(monkeypatch, gets=pages, posts=[LOGIN_OK])
    assert len(client.episodes(81189)) == 501
    assert [c[2]["params"]["page"] for c in http.calls if c[0] == "GET"] == [0, 1]


def test_tvdb_partial_episode_listing_is_discarded(monkeypatch):
    """A listing that breaks after the first page must not hand out wrong numbers."""
    client, _ = _tvdb(monkeypatch, gets=[_Response({"data": {"episodes": [{"id": i} for i in range(500)]}}), _Response(status=404)], posts=[LOGIN_OK])
    assert client.episodes(1) == []


def test_tvdb_translation_is_empty_when_missing(monkeypatch):
    client, _ = _tvdb(monkeypatch, gets=[_Response(status=404)], posts=[LOGIN_OK])
    assert client.translation("movies", 1) == {}
