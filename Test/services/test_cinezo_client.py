import pytest

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


def test_stream_resolution_is_explicitly_pending():
    with pytest.raises(RuntimeError, match="Media source resolution is not implemented"):
        cinezo_client.get_stream(27205, "movie")
