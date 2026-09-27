# 27.09.26

import pytest

from VibraVid.player.browser_manifest import BrowserManifestResolver
from VibraVid.player.resolver import (
    BrowserResolution,
    InteractionRequiredError,
)


class FakeBrowser:
    def __init__(self):
        self.events = []
        self.closed = False

    def open(self, url, headers):
        self.events.append(("open", url, dict(headers)))

    def wait_for_media_request(self, *, predicate, timeout):
        self.events.append(("wait", timeout))

        rendition_url = (
            "https://vixsrc.to/playlist/695377"
            "?type=video&rendition=720p&token=video-token"
        )
        response_headers = {
            "content-type": "application/vnd.apple.mpegurl",
        }

        assert predicate(rendition_url, response_headers) is False

        master_url = (
            "https://vixsrc.to/playlist/695377"
            "?b=1&token=master-token&expires=123456&h=1&lang=it"
        )

        assert predicate(master_url, response_headers) is True

        return BrowserResolution(
            media_url=master_url,
            headers={
                "accept": "*/*",
                "referer": (
                    "https://vixsrc.to/embed/695377"
                    "?token=embed-token&lang=it"
                ),
                "user-agent": "Chromium/Test",
                "sec-fetch-mode": "cors",
            },
        )

    def close(self):
        self.closed = True
        self.events.append(("close",))


def test_browser_manifest_resolver_returns_master_and_real_request_headers():
    browser = FakeBrowser()

    resolver = BrowserManifestResolver(
        browser_factory=lambda: browser,
        timeout=15.0,
    )

    manifest, headers = resolver.resolve(
        "https://vixsrc.to/movie/tt32897959?lang=it",
        {
            "User-Agent": "VibraVidGeneric/1.0",
            "Referer": "https://cineblog.example/movie",
        },
    )

    assert manifest.startswith(
        "https://vixsrc.to/playlist/695377?b=1"
    )
    assert headers["user-agent"] == "Chromium/Test"
    assert headers["referer"].startswith(
        "https://vixsrc.to/embed/695377"
    )

    # Higher-level HTTP headers must not overwrite Chromium's browser
    # fingerprint.
    assert browser.events[0] == (
        "open",
        "https://vixsrc.to/movie/tt32897959?lang=it",
        {},
    )

    assert browser.closed is True


def test_browser_manifest_resolver_accepts_regular_m3u8_master():
    assert BrowserManifestResolver._looks_like_master_manifest(
        "https://cdn.example.test/master.m3u8?token=test",
        {"content-type": "application/octet-stream"},
    ) is True


def test_browser_manifest_resolver_rejects_vixsrc_renditions():
    headers = {
        "content-type": "application/vnd.apple.mpegurl",
    }

    for rendition_type in ("video", "audio", "subtitle"):
        url = (
            "https://vixsrc.to/playlist/695377"
            f"?type={rendition_type}&rendition=test"
        )
        assert (
            BrowserManifestResolver._looks_like_master_manifest(
                url,
                headers,
            )
            is False
        )


def test_browser_manifest_resolver_always_closes_browser_on_failure():
    class FailingBrowser(FakeBrowser):
        def wait_for_media_request(self, *, predicate, timeout):
            raise InteractionRequiredError("no manifest")

    browser = FailingBrowser()
    resolver = BrowserManifestResolver(
        browser_factory=lambda: browser,
    )

    with pytest.raises(InteractionRequiredError, match="no manifest"):
        resolver.resolve(
            "https://vixsrc.to/movie/tt32897959?lang=it",
            {},
        )

    assert browser.closed is True
