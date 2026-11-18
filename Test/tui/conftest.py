# 01.08.26

import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_cover_lookups(monkeypatch):
    """Cover art goes to TMDB and to image hosts: no TUI test may reach the network."""
    from VibraVid.tui import artwork

    monkeypatch.setattr(artwork, "ENABLED", False)
