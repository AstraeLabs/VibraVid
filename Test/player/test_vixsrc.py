# 27.09.26

from VibraVid.player.vixsrc import VixSrcSource


def test_vixsrc_accepts_supported_player_host():
    source = VixSrcSource(
        "https://vixsrc.to/movie/tt32897959?lang=it",
        referer="https://cineblog001.download/cb01-streaming/example.html",
    )

    assert source.is_supported_player() is True
    assert source.get_stream() == (None, {})


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
    assert headers["X-Player-Referer"] == (
        "https://cineblog001.download/cb01-streaming/example.html"
    )


def test_vixsrc_handoff_rejects_non_hls_url():
    source = VixSrcSource("https://vixsrc.to/movie/tt32897959?lang=it")

    assert source.from_manifest("https://media.example.test/video.mp4") == (None, {})
