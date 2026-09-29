import pytest

from VibraVid.player.cinezo import CinezoResolverChain, CinezoStream
from VibraVid.services.cinezo import client as cinezo_client


def test_movie_player_url():
    assert cinezo_client.get_player_url(27205, "movie") == (
        "https://player.cinezo.live/embed/movie/27205"
    )


def test_tv_player_url():
    assert cinezo_client.get_player_url(1399, "tv", 1, 2) == (
        "https://player.cinezo.live/embed/tv/1399/1/2"
    )


def test_tv_player_url_requires_episode_coordinates():
    with pytest.raises(ValueError, match="season and episode are required"):
        cinezo_client.get_player_url(1399, "tv")


def test_stream_resolution_reports_missing_resolver_result():
    with pytest.raises(RuntimeError, match="No source resolver produced a playable stream"):
        cinezo_client.get_stream(27205, "movie")


def test_get_stream_returns_normalized_resolver_output():
    class _Resolver:
        name = "fixture"

        def resolve(self, tmdb_id, media_type, season=None, episode=None):
            return CinezoStream(
                url="https://example.test/master.m3u8",
                headers={"Referer": "https://example.test/"},
                subtitles=[{"type": "subtitle", "url": "https://example.test/it.vtt"}],
            )

    stream_url, headers, subtitles = cinezo_client.get_stream(
        27205,
        "movie",
        resolver_chain=CinezoResolverChain([_Resolver()]),
    )

    assert stream_url == "https://example.test/master.m3u8"
    assert headers == {"Referer": "https://example.test/"}
    assert subtitles == [{"type": "subtitle", "url": "https://example.test/it.vtt"}]
