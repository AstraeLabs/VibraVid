# 27.09.26

import pytest

from VibraVid.player.resolver import (
    BrowserEventPlayerResolver,
    BrowserResolution,
    HttpResponse,
    InteractionPlan,
    InvalidManifestError,
    ManifestNotFoundError,
    ResolvedStream,
    Resolver,
    StaticHtmlPlayerResolver,
    redact_headers,
)


class FakeHttpClient:
    def __init__(self, responses: dict[str, HttpResponse]):
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse:
        self.calls.append((url, dict(headers or {})))

        if url not in self.responses:
            raise RuntimeError(f"No fake response for {url}")

        return self.responses[url]


def test_resolves_static_hls_manifest():
    player_url = "https://example.com/player-x/movie/tt1234567?lang=it"
    manifest_url = "https://cdn.example.com/video/master.m3u8"

    http = FakeHttpClient(
        {
            player_url: HttpResponse(
                url=player_url,
                status_code=200,
                text=f'<div data-manifest="{manifest_url}"></div>',
                headers={"content-type": "text/html"},
            ),
            manifest_url: HttpResponse(
                url=manifest_url,
                status_code=200,
                text="#EXTM3U\n#EXT-X-VERSION:3\n",
                headers={"content-type": "application/vnd.apple.mpegurl"},
            ),
        }
    )

    page_resolver = StaticHtmlPlayerResolver(http)
    resolver = Resolver(
        http,
        page_resolver,
        user_agent="ResolverTest/1.0",
    )

    result = resolver.resolve(player_url)

    assert result == ResolvedStream(
        manifest_url=manifest_url,
        headers={
            "User-Agent": "ResolverTest/1.0",
            "Referer": player_url,
        },
        is_hls=True,
    )


def test_relative_manifest_url_is_resolved_against_player_page():
    player_url = "https://example.com/player-x/movie/tt1234567"
    manifest_url = "https://example.com/player-x/media/master.m3u8"

    http = FakeHttpClient(
        {
            player_url: HttpResponse(
                url=player_url,
                status_code=200,
                text='<div data-manifest="./media/master.m3u8"></div>',
                headers={"content-type": "text/html"},
            ),
            manifest_url: HttpResponse(
                url=manifest_url,
                status_code=200,
                text="#EXTM3U\n",
                headers={"content-type": "text/plain"},
            ),
        }
    )

    result = Resolver(
        http,
        StaticHtmlPlayerResolver(http),
        user_agent="ResolverTest/1.0",
    ).resolve(player_url)

    assert result.manifest_url == manifest_url
    assert result.is_hls is True


def test_preserves_session_headers_from_page_resolver():
    player_url = "https://example.com/player-x/movie/tt1234567?lang=it"
    manifest_url = "https://cdn.example.com/video/master.m3u8"

    class FakePageResolver:
        def resolve_candidate(self, url, *, headers):
            return (
                manifest_url,
                {
                    "Referer": "https://example.com/player-x/",
                    "Cookie": "session=fake-session-cookie",
                    "X-Player-Token": "fake-token",
                },
            )

    http = FakeHttpClient(
        {
            manifest_url: HttpResponse(
                url=manifest_url,
                status_code=200,
                text="#EXTM3U\n",
                headers={"content-type": "application/x-mpegURL"},
            )
        }
    )

    resolver = Resolver(
        http,
        FakePageResolver(),
        user_agent="ResolverTest/1.0",
    )

    result = resolver.resolve(player_url)

    assert result.is_hls is True
    assert result.headers["Cookie"] == "session=fake-session-cookie"
    assert result.headers["Referer"] == "https://example.com/player-x/"
    assert result.headers["X-Player-Token"] == "fake-token"


def test_returns_non_hls_source_without_claiming_hls():
    player_url = "https://example.com/player-x/movie/tt1234567?lang=it"
    media_url = "https://cdn.example.com/video/file.mp4"

    class FakePageResolver:
        def resolve_candidate(self, url, *, headers):
            return media_url, {}

    http = FakeHttpClient(
        {
            media_url: HttpResponse(
                url=media_url,
                status_code=200,
                text="not a playlist",
                headers={"content-type": "video/mp4"},
            )
        }
    )

    result = Resolver(
        http,
        FakePageResolver(),
        user_agent="ResolverTest/1.0",
    ).resolve(player_url)

    assert result.manifest_url == media_url
    assert result.is_hls is False


def test_rejects_invalid_manifest_http_status():
    player_url = "https://example.com/player-x/movie/tt1234567?lang=it"
    manifest_url = "https://cdn.example.com/video/master.m3u8"

    class FakePageResolver:
        def resolve_candidate(self, url, *, headers):
            return manifest_url, {}

    http = FakeHttpClient(
        {
            manifest_url: HttpResponse(
                url=manifest_url,
                status_code=403,
                text="Forbidden",
                headers={"content-type": "text/plain"},
            )
        }
    )

    with pytest.raises(InvalidManifestError, match="HTTP 403"):
        Resolver(
            http,
            FakePageResolver(),
            user_agent="ResolverTest/1.0",
        ).resolve(player_url)


def test_static_html_resolver_raises_when_no_candidate_exists():
    player_url = "https://example.com/player-x/movie/tt1234567?lang=it"

    http = FakeHttpClient(
        {
            player_url: HttpResponse(
                url=player_url,
                status_code=200,
                text="<html><body>No player data</body></html>",
                headers={"content-type": "text/html"},
            )
        }
    )

    page_resolver = StaticHtmlPlayerResolver(http)

    with pytest.raises(ManifestNotFoundError):
        page_resolver.resolve_candidate(
            player_url,
            headers={"User-Agent": "ResolverTest/1.0"},
        )


def test_browser_event_resolver_uses_nested_frames_and_trigger():
    events = []

    class FakeBrowser:
        def open(self, url, headers):
            events.append(("open", url))

        def enter_frame(self, selector):
            events.append(("frame", selector))

        def trigger(self, selector):
            events.append(("trigger", selector))

        def wait_for_media_request(self, *, predicate, timeout):
            events.append(("wait", timeout))

            url = "https://cdn.example.com/master.m3u8"
            headers = {
                "content-type": "application/vnd.apple.mpegurl",
                "Referer": "https://example.com/player-x/",
                "Cookie": "session=fake",
                "User-Agent": "FakeBrowser/1.0",
            }

            assert predicate(url, headers)

            return BrowserResolution(
                media_url=url,
                headers=headers,
            )

        def close(self):
            events.append(("close",))

    page_resolver = BrowserEventPlayerResolver(
        browser_factory=FakeBrowser,
        plan=InteractionPlan(
            frame_selectors=(
                "iframe.outer",
                "iframe.inner",
            ),
            trigger_selectors=("#player-overlay",),
        ),
    )

    url, headers = page_resolver.resolve_candidate(
        "https://example.com/player-x/movie/tt1234567",
        headers={"User-Agent": "ResolverTest/1.0"},
    )

    assert url == "https://cdn.example.com/master.m3u8"
    assert headers["Cookie"] == "session=fake"

    assert events == [
        ("open", "https://example.com/player-x/movie/tt1234567"),
        ("frame", "iframe.outer"),
        ("frame", "iframe.inner"),
        ("trigger", "#player-overlay"),
        ("wait", 20.0),
        ("close",),
    ]


def test_redact_headers_hides_session_values():
    redacted = redact_headers(
        {
            "Cookie": "session=secret",
            "Authorization": "Bearer secret",
            "Referer": "https://example.com/player-x/",
        }
    )

    assert redacted == {
        "Cookie": "<redacted>",
        "Authorization": "<redacted>",
        "Referer": "https://example.com/player-x/",
    }
