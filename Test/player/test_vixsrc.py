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
