import pytest

from VibraVid.player.cinezo import CinezoSourceProbe
from VibraVid.player import cinezo_media


def test_media_resolver_rejects_unavailable_source():
    source = CinezoSourceProbe(
        name="berlin",
        endpoint="https://example.test/berlin",
        available=False,
    )
    with pytest.raises(RuntimeError, match="Source backend is not available"):
        cinezo_media.resolve_cinezo_media(source, 27205, "movie")


def test_media_resolver_requires_tv_coordinates():
    source = CinezoSourceProbe(
        name="berlin",
        endpoint="https://example.test/berlin",
        available=True,
    )
    with pytest.raises(ValueError, match="season and episode are required"):
        cinezo_media.resolve_cinezo_media(source, 1399, "tv")


def test_media_resolver_reaches_single_todo():
    source = CinezoSourceProbe(
        name="berlin",
        endpoint="https://example.test/berlin",
        available=True,
    )
    with pytest.raises(RuntimeError, match="Media resolver TODO for backend berlin"):
        cinezo_media.resolve_cinezo_media(source, 27205, "movie")
