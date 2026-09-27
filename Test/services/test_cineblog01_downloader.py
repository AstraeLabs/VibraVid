# 27.09.26

from VibraVid.services.cineblog01 import downloader


def make_vixsrc_source():
    return downloader.CineblogSource(
        section="player",
        label="VixSrc",
        host="vixsrc.to",
        url="https://vixsrc.to/movie/tt32897959?lang=it",
    )


def test_vixsrc_uses_default_browser_manifest_resolver(monkeypatch):
    source = make_vixsrc_source()

    calls = []

    class FakeBrowserManifestResolver:
        def resolve(self, player_url, player_headers):
            calls.append((player_url, dict(player_headers)))
            return (
                "https://vixsrc.to/playlist/695377?b=1&token=test",
                {
                    "referer": "https://vixsrc.to/embed/695377?token=test",
                    "user-agent": "Chromium/Test",
                },
            )

    monkeypatch.setattr(
        "VibraVid.player.browser_manifest.BrowserManifestResolver",
        FakeBrowserManifestResolver,
    )

    manifest, headers = downloader._resolve_source(
        source,
        "https://cineblog.example/movie",
    )

    assert manifest == (
        "https://vixsrc.to/playlist/695377?b=1&token=test"
    )
    assert headers["user-agent"] == "Chromium/Test"
    assert headers["referer"].startswith(
        "https://vixsrc.to/embed/695377"
    )
    assert calls[0][0] == source.url


def test_vixsrc_preserves_explicit_manifest_resolver(monkeypatch):
    source = make_vixsrc_source()

    class UnexpectedDefaultResolver:
        def __init__(self):
            raise AssertionError(
                "default BrowserManifestResolver must not be created"
            )

    monkeypatch.setattr(
        "VibraVid.player.browser_manifest.BrowserManifestResolver",
        UnexpectedDefaultResolver,
    )

    calls = []

    def injected_resolver(player_url, player_headers):
        calls.append((player_url, dict(player_headers)))
        return (
            "https://vixsrc.to/playlist/695377?b=1&token=injected",
            {
                "referer": "https://vixsrc.to/embed/695377?token=injected",
                "user-agent": "Injected/Test",
            },
        )

    manifest, headers = downloader._resolve_source(
        source,
        "https://cineblog.example/movie",
        manifest_resolver=injected_resolver,
    )

    assert manifest.endswith("token=injected")
    assert headers["user-agent"] == "Injected/Test"
    assert len(calls) == 1
