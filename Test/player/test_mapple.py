import hashlib

import pytest

from VibraVid.player.mapple import MappleResolver, get_player_url


class FakeResponse:
    def __init__(self, status_code=200, payload=None, *, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, *, pow_required=False, tv_fallback=False):
        self.pow_required = pow_required
        self.tv_fallback = tv_fallback
        self.calls = []
        self.playback_calls = 0

    def close(self):
        pass

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))

        if "/watch/tv/" in url and self.tv_fallback and "/" in url.rsplit("/watch/tv/", 1)[1]:
            return FakeResponse(404, headers={"content-type": "text/html"})

        if "/watch/" in url:
            return FakeResponse(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html></html>",
            )

        if "/api/stream-encrypted" in url:
            if "data=mapple" in url:
                return FakeResponse(
                    200,
                    {"success": False, "data": None},
                    headers={"content-type": "application/json"},
                )
            return FakeResponse(
                200,
                {
                    "success": True,
                    "data": {
                        "stream_url": "https://cdn.example/playlist/master.m3u8?e=1&s=2"
                    },
                },
                headers={"content-type": "application/json"},
            )

        raise AssertionError(f"Unexpected GET {url}")

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))

        if url.endswith("/api/request-token"):
            return FakeResponse(200, {"token": "request-token"})

        if url.endswith("/api/playback-init"):
            self.playback_calls += 1
            if self.pow_required and self.playback_calls == 1:
                return FakeResponse(
                    200,
                    {
                        "success": True,
                        "requiresPow": True,
                        "pow": {
                            "challengeId": "challenge-id",
                            "challenge": "challenge",
                            "difficulty": 18,
                        },
                    },
                )
            return FakeResponse(
                200,
                {
                    "success": True,
                    "token": "playback-token",
                    "expiresIn": 120,
                },
            )

        if url.endswith("/api/encrypt"):
            source = kwargs["json"]["data"]["source"]
            return FakeResponse(
                200,
                {"url": f"/api/stream-encrypted?data={source}"},
            )

        raise AssertionError(f"Unexpected POST {url}")


def _client_factory(session):
    def factory(**kwargs):
        return session

    return factory


def test_get_player_url():
    assert get_player_url(27205, "movie") == "https://mapple.fun/watch/movie/27205"
    assert (
        get_player_url(1399, "tv", 1, 2)
        == "https://mapple.fun/watch/tv/1399/1/2"
    )


def test_pow_solver_meets_requested_difficulty():
    challenge = "mapple-test"
    nonce = MappleResolver.solve_pow(
        challenge,
        10,
        max_attempts=100_000,
    )
    digest = hashlib.sha256((challenge + nonce).encode()).digest()
    assert MappleResolver._leading_zero_bits(digest) >= 10


def test_movie_resolver_falls_back_to_second_source():
    session = FakeSession()
    resolver = MappleResolver(
        sources=("mapple", "s2"),
        client_factory=_client_factory(session),
        user_agent="pytest",
    )

    result = resolver.resolve_stream(27205, "movie")

    assert result.source == "s2"
    assert result.url.startswith("https://cdn.example/playlist/master.m3u8")
    assert result.headers["Origin"] == "https://mapple.fun"
    assert result.headers["Referer"] == "https://mapple.fun/"


def test_tv_resolver_submits_pow_and_tv_slug():
    session = FakeSession(pow_required=True, tv_fallback=True)
    resolver = MappleResolver(
        sources=("s2",),
        client_factory=_client_factory(session),
        user_agent="pytest",
    )
    resolver.solve_pow = lambda challenge, difficulty: "123"

    result = resolver.resolve_stream(
        1399,
        "tv",
        season=2,
        episode=3,
    )

    assert result.source == "s2"

    playback_payloads = [
        kwargs["json"]
        for method, url, kwargs in session.calls
        if method == "POST" and url.endswith("/api/playback-init")
    ]
    assert playback_payloads[0]["tv_slug"] == "2-3"
    assert playback_payloads[1]["pow"] == {
        "challengeId": "challenge-id",
        "nonce": "123",
    }

    watch_urls = [
        url
        for method, url, _ in session.calls
        if method == "GET" and "/watch/tv/" in url
    ]
    assert watch_urls == [
        "https://mapple.fun/watch/tv/1399/2/3",
        "https://mapple.fun/watch/tv/1399-2-3",
    ]


def test_tv_requires_coordinates():
    resolver = MappleResolver(
        sources=("mapple",),
        client_factory=lambda **kwargs: FakeSession(),
        user_agent="pytest",
    )

    with pytest.raises(ValueError, match="season and episode"):
        resolver.resolve_stream(1399, "tv")
