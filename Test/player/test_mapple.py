import hashlib

import pytest

from VibraVid.player.mapple import MappleResolver, get_player_url


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        payload=None,
        *,
        headers=None,
        text="",
        content=None,
    ):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text
        self.content = content if content is not None else text.encode()

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

        if url.startswith("https://cdn.example/playlist/"):
            return FakeResponse(
                200,
                headers={"content-type": "application/vnd.apple.mpegurl"},
                text="#EXTM3U\n#EXT-X-VERSION:3\n",
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


def _client_factory(session, captured_kwargs=None):
    def factory(**kwargs):
        if captured_kwargs is not None:
            captured_kwargs.append(kwargs)
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


def test_resolver_disables_browser_impersonation():
    captured = []
    session = FakeSession()
    resolver = MappleResolver(
        sources=("s2",),
        client_factory=_client_factory(session, captured),
        user_agent="pytest",
    )

    resolver.resolve_stream(27205, "movie")

    assert captured
    assert captured[0]["browser"] is None


def test_manifest_probe_rejects_unusable_source():
    class ManifestFallbackSession(FakeSession):
        def get(self, url, **kwargs):
            if url.startswith("https://bad.example/"):
                self.calls.append(("GET", url, kwargs))
                return FakeResponse(
                    404,
                    headers={"content-type": "text/plain"},
                    text="not found",
                )

            return super().get(url, **kwargs)

        def post(self, url, **kwargs):
            if url.endswith("/api/encrypt"):
                self.calls.append(("POST", url, kwargs))
                source = kwargs["json"]["data"]["source"]
                return FakeResponse(
                    200,
                    {"url": f"/api/stream-encrypted?data={source}"},
                )
            return super().post(url, **kwargs)

    session = ManifestFallbackSession()

    original_get = session.get

    def get_with_bad_first(url, **kwargs):
        if "/api/stream-encrypted" in url and "data=mapple" in url:
            session.calls.append(("GET", url, kwargs))
            return FakeResponse(
                200,
                {
                    "success": True,
                    "data": {"stream_url": "https://bad.example/master.m3u8"},
                },
                headers={"content-type": "application/json"},
            )
        return original_get(url, **kwargs)

    session.get = get_with_bad_first

    resolver = MappleResolver(
        sources=("mapple", "s2"),
        client_factory=_client_factory(session),
        user_agent="pytest",
    )

    result = resolver.resolve_stream(27205, "movie")

    assert result.source == "s2"


def test_best_variant_url_prefers_highest_bandwidth():
    content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080
high.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1280x720
mid.m3u8
"""

    assert (
        MappleResolver._best_variant_url(
            "https://cdn.example/playlist/master.m3u8?token=abc",
            content,
        )
        == "https://cdn.example/playlist/high.m3u8"
    )


def test_manifest_probe_retries_transient_child_404(monkeypatch):
    class TransientVariantSession:
        def __init__(self):
            self.variant_calls = 0

        def get(self, url, **kwargs):
            if url.endswith("master.m3u8"):
                return FakeResponse(
                    200,
                    headers={"content-type": "application/vnd.apple.mpegurl"},
                    text=(
                        "#EXTM3U\n"
                        "#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080\n"
                        "high.m3u8\n"
                    ),
                )

            if url.endswith("high.m3u8"):
                self.variant_calls += 1
                if self.variant_calls < 3:
                    return FakeResponse(404, text="not found")
                return FakeResponse(
                    200,
                    headers={"content-type": "application/vnd.apple.mpegurl"},
                    text=(
                        "#EXTM3U\n"
                        "#EXTINF:5.0,\n"
                        "seg-1.ts\n"
                        "#EXTINF:5.0,\n"
                        "seg-2.ts\n"
                        "#EXTINF:5.0,\n"
                        "seg-3.ts\n"
                        "#EXT-X-ENDLIST\n"
                    ),
                )

            if "seg-" in url:
                return FakeResponse(200, content=b"media")

            raise AssertionError(url)

    monkeypatch.setattr("VibraVid.player.mapple.time.sleep", lambda _: None)

    session = TransientVariantSession()
    resolver = MappleResolver(
        sources=("mapple",),
        client_factory=lambda **kwargs: session,
        user_agent="pytest",
    )

    assert resolver._manifest_is_playable(
        session,
        "https://cdn.example/master.m3u8",
    )
    assert session.variant_calls == 3


def test_manifest_probe_rejects_source_with_dead_media_segment(monkeypatch):
    class DeadMediaSession:
        def get(self, url, **kwargs):
            if url.endswith("master.m3u8"):
                return FakeResponse(
                    200,
                    text=(
                        "#EXTM3U\n"
                        "#EXT-X-STREAM-INF:BANDWIDTH=4000000\n"
                        "high.m3u8\n"
                    ),
                )

            if url.endswith("high.m3u8"):
                return FakeResponse(
                    200,
                    text=(
                        "#EXTM3U\n"
                        "#EXTINF:5.0,\n"
                        "seg-1.ts\n"
                        "#EXTINF:5.0,\n"
                        "seg-2.ts\n"
                        "#EXTINF:5.0,\n"
                        "seg-3.ts\n"
                        "#EXT-X-ENDLIST\n"
                    ),
                )

            if url.endswith("seg-1.ts"):
                return FakeResponse(200, content=b"media")

            if url.endswith("seg-2.ts"):
                return FakeResponse(404, text="not found")

            if url.endswith("seg-3.ts"):
                return FakeResponse(200, content=b"media")

            raise AssertionError(url)

    monkeypatch.setattr("VibraVid.player.mapple.time.sleep", lambda _: None)

    session = DeadMediaSession()
    resolver = MappleResolver(
        sources=("mapple",),
        client_factory=lambda **kwargs: session,
        user_agent="pytest",
    )

    assert not resolver._manifest_is_playable(
        session,
        "https://cdn.example/master.m3u8",
    )


def test_source_resolution_retries_fresh_stream_after_transient_manifest(monkeypatch):
    class FreshStreamSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.stream_calls = 0

        def get(self, url, **kwargs):
            if "/api/stream-encrypted" in url and "data=s2" in url:
                self.calls.append(("GET", url, kwargs))
                self.stream_calls += 1
                stream_url = (
                    "https://bad.example/master.m3u8"
                    if self.stream_calls == 1
                    else "https://cdn.example/playlist/master.m3u8"
                )
                return FakeResponse(
                    200,
                    {
                        "success": True,
                        "data": {"stream_url": stream_url},
                    },
                    headers={"content-type": "application/json"},
                )

            if url == "https://bad.example/master.m3u8":
                self.calls.append(("GET", url, kwargs))
                return FakeResponse(404, text="not found")

            return super().get(url, **kwargs)

    monkeypatch.setattr("VibraVid.player.mapple.time.sleep", lambda _: None)

    session = FreshStreamSession()
    resolver = MappleResolver(
        sources=("s2",),
        client_factory=_client_factory(session),
        user_agent="pytest",
    )

    result = resolver.resolve_stream(27205, "movie")

    assert result.source == "s2"
    assert result.url.startswith("https://cdn.example/playlist/master.m3u8")
    assert session.stream_calls == 2
