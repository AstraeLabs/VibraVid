# 06.10.26

"""The TMDB API key must never reach the log file or the console, not even inside an HTTP error message."""

import logging

import pytest

from VibraVid.provider import tmdb

SECRET = "s3cr3t-tmdb-key-0123456789abcdef"


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"results": [1, 2]}


class _Client:
    def __init__(self, fail=False):
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None):
        if self.fail:
            raise RuntimeError(f"Client error '401 Unauthorized' for url '{url}?api_key={params['api_key']}&language=it'")
        return _Response()


class _Console:
    def __init__(self):
        self.lines = []

    def log(self, message, *args, **kwargs):
        self.lines.append(str(message))


@pytest.fixture
def client(monkeypatch):
    return tmdb.TMDBClient(api_key=SECRET)


def test_request_log_masks_the_key_but_keeps_the_other_params(monkeypatch, client, caplog):
    monkeypatch.setattr(tmdb, "create_client", lambda **kwargs: _Client())
    caplog.set_level(logging.DEBUG, logger=tmdb.logger.name)

    assert client._make_request("search/tv", {"query": "brothers", "language": "it"}) == {"results": [1, 2]}

    assert SECRET not in caplog.text
    assert "query" in caplog.text and "brothers" in caplog.text and "api_key" in caplog.text


def test_the_request_itself_still_carries_the_real_key(monkeypatch, client):
    seen = {}

    class Spy(_Client):
        def get(self, url, params=None):
            seen.update(params)
            return super().get(url, params)

    monkeypatch.setattr(tmdb, "create_client", lambda **kwargs: Spy())

    client._make_request("tv/1", {})

    assert seen["api_key"] == SECRET


def test_http_error_messages_are_masked_before_they_are_shown(monkeypatch, client):
    fake_console = _Console()
    monkeypatch.setattr(tmdb, "create_client", lambda **kwargs: _Client(fail=True))
    monkeypatch.setattr(tmdb, "console", fake_console)

    assert client._make_request("search/tv", {}, retries=0) == {}

    assert fake_console.lines and all(SECRET not in line for line in fake_console.lines)
    assert any("401" in line for line in fake_console.lines)


def test_redact_helper_only_touches_secret_params():
    assert tmdb._redact_params({"api_key": SECRET, "query": "x"}) == {"api_key": "***", "query": "x"}
    assert tmdb._redact_params({}) == {}
