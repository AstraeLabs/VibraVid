import pytest

from VibraVid.player.cinezo import CinezoResolverChain, CinezoSourceProbe
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


def test_probe_sources_delegates_to_chain():
    class _Chain:
        def probe_sources(self, tmdb_id, media_type, season=None, episode=None):
            return [
                CinezoSourceProbe(
                    name="fixture",
                    endpoint="https://example.test/source",
                    status_code=200,
                    content_type="application/json",
                    available=True,
                    source_shape="source",
                    source_keys=["type", "url"],
                )
            ]

    results = cinezo_client.probe_sources(
        27205,
        "movie",
        resolver_chain=_Chain(),
    )

    assert len(results) == 1
    assert results[0].name == "fixture"
    assert results[0].available is True


def test_get_stream_reports_available_backend_without_exposing_media(monkeypatch):
    monkeypatch.setattr(
        cinezo_client,
        "probe_sources",
        lambda *args, **kwargs: [
            CinezoSourceProbe(
                name="zendaya",
                endpoint="https://example.test/source",
                available=True,
            )
        ],
    )

    with pytest.raises(RuntimeError, match="Source backend available \(zendaya\)"):
        cinezo_client.get_stream(27205, "movie")


def test_get_stream_reports_source_failures(monkeypatch):
    monkeypatch.setattr(
        cinezo_client,
        "probe_sources",
        lambda *args, **kwargs: [
            CinezoSourceProbe(
                name="jennifer",
                endpoint="https://example.test/source",
                status_code=530,
                error="HTTP 530",
            )
        ],
    )

    with pytest.raises(RuntimeError, match="jennifer=HTTP 530"):
        cinezo_client.get_stream(27205, "movie")
