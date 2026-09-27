# 27.09.26

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from VibraVid.player.browser import PlaywrightBrowserSession
from VibraVid.player.resolver import (
    BrowserResolution,
    ManifestNotFoundError,
)


class BrowserManifestResolver:
    """Resolve an HLS master manifest by observing browser network traffic.

    The browser intentionally uses its native Chromium request fingerprint.
    Player headers supplied by higher-level HTTP resolvers are not injected
    into the browser context because overriding User-Agent, Referer or related
    navigation headers can alter player behaviour.

    The playback headers returned to the caller are the real request headers
    observed on the matching manifest request.
    """

    def __init__(
        self,
        *,
        browser_factory=None,
        timeout: float = 20.0,
        headless: bool = True,
    ):
        self.timeout = timeout
        self.headless = headless
        self.browser_factory = browser_factory

    def _new_browser(self):
        if self.browser_factory is not None:
            return self.browser_factory()

        return PlaywrightBrowserSession(
            headless=self.headless,
        )

    def resolve(
        self,
        player_url: str,
        player_headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Return master manifest URL and the browser playback headers."""

        browser = self._new_browser()

        try:
            # Deliberately do not inject player_headers here.
            #
            # VixSrc changes behaviour when the Chromium fingerprint is
            # overwritten with VibraVid's generic HTTP User-Agent/Referer.
            browser.open(player_url, {})

            result = browser.wait_for_media_request(
                predicate=self._looks_like_master_manifest,
                timeout=self.timeout,
            )

            if not result.media_url:
                raise ManifestNotFoundError(
                    "Browser did not expose an HLS master manifest"
                )

            return result.media_url, dict(result.headers)

        finally:
            browser.close()

    @staticmethod
    def _looks_like_master_manifest(
        url: str,
        response_headers: dict[str, str],
    ) -> bool:
        parsed = urlsplit(url)
        path = parsed.path.lower()
        content_type = response_headers.get("content-type", "").lower()

        is_hls = (
            path.endswith(".m3u8")
            or "mpegurl" in content_type
        )

        if not is_hls:
            return False

        host = (parsed.hostname or "").lower()

        # VixSrc exposes the master and each rendition through /playlist/.
        # Renditions carry ?type=video, ?type=audio or ?type=subtitle.
        # The master does not.
        if host == "vixsrc.to" or host.endswith(".vixsrc.to"):
            if path.startswith("/playlist/"):
                query = parse_qs(parsed.query, keep_blank_values=True)
                if "type" in query:
                    return False

        return True
