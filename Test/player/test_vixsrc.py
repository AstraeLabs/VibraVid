# 27.09.26

from VibraVid.player.vixsrc import VixSrcSource


def test_vixsrc_accepts_supported_player_host():
    source = VixSrcSource(
        "https://vixsrc.to/movie/tt32897959?lang=it",
        referer="https://cineblog001.download/cb01-streaming/example.html",
    )

    assert source.is_supported_player() is True


def test_vixsrc_rejects_unrelated_player_host():
    source = VixSrcSource("https://example.com/movie/tt32897959")

    assert source.is_supported_player() is False
    assert source.get_stream() == (None, {})


def test_vixsrc_handoff_accepts_resolved_hls_manifest():
    source = VixSrcSource(
        "https://vixsrc.to/movie/tt32897959?lang=it",
        referer="https://cineblog001.download/cb01-streaming/example.html",
    )

    manifest, headers = source.from_manifest(
        "https://media.example.test/path/master.m3u8?token=test"
    )

    assert manifest == "https://media.example.test/path/master.m3u8?token=test"
    assert headers["Referer"] == "https://vixsrc.to/movie/tt32897959?lang=it"
    assert headers["Origin"] == "https://vixsrc.to"

    player_headers = source.get_player_headers()
    assert player_headers["Referer"] == (
        "https://cineblog001.download/cb01-streaming/example.html"
    )


def test_vixsrc_handoff_rejects_non_hls_url():
    source = VixSrcSource("https://vixsrc.to/movie/tt32897959?lang=it")

    assert source.from_manifest("https://media.example.test/video.mp4") == (None, {})


class FakeHttpResponse:
    def __init__(self, url, status_code, text, headers):
        self.url = url
        self.status_code = status_code
        self.text = text
        self.headers = headers


class FakeHttpClient:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, *, headers=None, timeout=20.0):
        return self.responses[url]


def test_vixsrc_resolves_directly_exposed_hls_manifest():
    player_url = "https://vixsrc.to/movie/tt32897959?lang=it"
    manifest_url = "https://media.example.test/master.m3u8"

    http = FakeHttpClient(
        {
            player_url: FakeHttpResponse(
                player_url,
                200,
                f'<div data-manifest="{manifest_url}"></div>',
                {"content-type": "text/html"},
            ),
            manifest_url: FakeHttpResponse(
                manifest_url,
                200,
                "#EXTM3U\n#EXT-X-VERSION:3\n",
                {"content-type": "application/vnd.apple.mpegurl"},
            ),
        }
    )

    source = VixSrcSource(player_url, http_client=http)

    manifest, headers = source.get_stream()

    assert manifest == manifest_url
    assert headers["Referer"] == player_url


def test_vixsrc_returns_empty_when_player_has_no_static_manifest():
    player_url = "https://vixsrc.to/movie/tt32897959?lang=it"

    http = FakeHttpClient(
        {
            player_url: FakeHttpResponse(
                player_url,
                200,
                "<html><body>No static manifest</body></html>",
                {"content-type": "text/html"},
            )
        }
    )

    source = VixSrcSource(player_url, http_client=http)

    assert source.get_stream() == (None, {})


def test_vixsrc_falls_back_to_injected_browser_resolver():
    player_url = "https://vixsrc.to/movie/tt32897959?lang=it"
    manifest_url = "https://media.example.test/browser/master.m3u8"

    class FakeBrowser:
        def open(self, url, headers):
            assert url == player_url

        def enter_frame(self, selector):
            raise AssertionError("no frame expected")

        def trigger(self, selector):
            raise AssertionError("no trigger expected")

        def wait_for_media_request(self, *, predicate, timeout):
            from VibraVid.player.resolver import BrowserResolution

            headers = {
                "content-type": "application/vnd.apple.mpegurl",
                "Referer": player_url,
            }
            assert predicate(manifest_url, headers)
            return BrowserResolution(
                media_url=manifest_url,
                headers=headers,
            )

        def close(self):
            pass

    http = FakeHttpClient(
        {
            player_url: FakeHttpResponse(
                player_url,
                200,
                "<html><body>No static manifest</body></html>",
                {"content-type": "text/html"},
            ),
            manifest_url: FakeHttpResponse(
                manifest_url,
                200,
                "#EXTM3U\n#EXT-X-VERSION:3\n",
                {"content-type": "application/vnd.apple.mpegurl"},
            ),
        }
    )

    source = VixSrcSource(
        player_url,
        http_client=http,
        browser_factory=FakeBrowser,
    )

    manifest, headers = source.get_stream()

    assert manifest == manifest_url
    assert headers["Referer"] == player_url


def test_vixsrc_uses_injected_manifest_resolver_before_other_strategies():
    player_url = "https://vixsrc.to/movie/tt32897959?lang=it"
    manifest_url = "https://media.example.test/injected/master.m3u8"

    calls = []

    def manifest_resolver(url, headers):
        calls.append((url, headers))
        return manifest_url, {
            "Referer": player_url,
            "User-Agent": "InjectedResolver/1.0",
        }

    source = VixSrcSource(
        player_url,
        manifest_resolver=manifest_resolver,
    )

    manifest, headers = source.get_stream()

    assert manifest == manifest_url
    assert headers["Referer"] == player_url
    assert headers["User-Agent"] == "InjectedResolver/1.0"
    assert calls[0][0] == player_url


def test_vixsrc_rejects_non_hls_injected_manifest():
    player_url = "https://vixsrc.to/movie/tt32897959?lang=it"

    source = VixSrcSource(
        player_url,
        manifest_resolver=lambda url, headers: (
            "https://media.example.test/video.mp4",
            {},
        ),
    )

    assert source._resolve_injected_manifest() == (None, {})


def test_vixsrc_accepts_native_playlist_endpoint_as_hls_manifest():
    source = VixSrcSource(
        "https://vixsrc.to/movie/tt32897959?lang=it"
    )

    assert source.is_hls_manifest(
        "https://vixsrc.to/playlist/695377"
        "?b=1&token=test&expires=123456&h=1&lang=it"
    ) is True


def test_vixsrc_does_not_accept_unrelated_playlist_endpoint():
    source = VixSrcSource(
        "https://vixsrc.to/movie/tt32897959?lang=it"
    )

    assert source.is_hls_manifest(
        "https://example.test/playlist/695377?token=test"
    ) is False
