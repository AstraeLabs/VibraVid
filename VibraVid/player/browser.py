# 27.09.26

from __future__ import annotations

import time

from VibraVid.player.resolver import BrowserResolution, InteractionRequiredError


_IGNORED_PLAYBACK_HEADERS = {
    "host",
    "content-length",
    "connection",
}


class PlaywrightBrowserSession:
    """Generic Playwright-backed BrowserSession implementation.

    Playwright is imported lazily so normal VibraVid usage does not require it.
    A custom playwright_factory can be injected by tests.

    Response headers are used only to identify interesting media responses.
    BrowserResolution.headers contains the request headers that were actually
    used by Chromium for the matching media request, because those are the
    headers required by the downloader to reproduce the playback request.
    """

    def __init__(
        self,
        *,
        playwright_factory=None,
        headless: bool = True,
    ):
        self.playwright_factory = playwright_factory
        self.headless = headless
        self._manager = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._frame = None
        self._responses: list[
            tuple[BrowserResolution, dict[str, str]]
        ] = []

    def _start(self):
        if self._page is not None:
            return

        if self.playwright_factory is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as error:
                raise InteractionRequiredError(
                    "Playwright is required for browser-based player resolution"
                ) from error
            factory = sync_playwright
        else:
            factory = self.playwright_factory

        self._manager = factory()
        self._playwright = self._manager.start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )
        self._context = self._browser.new_context(
            service_workers="block",
            ignore_https_errors=False,
        )
        self._page = self._context.new_page()
        self._frame = self._page

        def on_response(response):
            response_headers = {
                str(key).lower(): str(value)
                for key, value in response.headers.items()
            }

            try:
                request_headers = response.request.all_headers()
            except Exception:
                request_headers = {}

            playback_headers = {
                str(key): str(value)
                for key, value in request_headers.items()
                if not str(key).startswith(":")
                and str(key).lower() not in _IGNORED_PLAYBACK_HEADERS
            }

            self._responses.append(
                (
                    BrowserResolution(
                        media_url=str(response.url),
                        headers=playback_headers,
                    ),
                    response_headers,
                )
            )

        self._page.on("response", on_response)

    def open(self, url: str, headers: dict[str, str]) -> None:
        self._start()

        if headers:
            self._context.set_extra_http_headers(dict(headers))

        response = self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        if response is None:
            raise InteractionRequiredError(
                "Browser navigation did not return an HTTP response"
            )

        if not 200 <= response.status < 300:
            raise InteractionRequiredError(
                f"Browser navigation returned HTTP {response.status}"
            )

    def enter_frame(self, selector: str) -> None:
        if self._frame is None:
            raise InteractionRequiredError("Browser session is not open")

        locator = self._frame.locator(selector)
        handle = locator.element_handle(timeout=10000)
        if handle is None:
            raise InteractionRequiredError(
                f"Frame selector not found: {selector}"
            )

        child = handle.content_frame()
        if child is None:
            raise InteractionRequiredError(
                f"Selector is not an iframe: {selector}"
            )

        self._frame = child

    def trigger(self, selector: str) -> None:
        if self._frame is None:
            raise InteractionRequiredError("Browser session is not open")

        locator = self._frame.locator(selector)
        locator.wait_for(state="attached", timeout=10000)
        locator.evaluate("(element) => element.click()")

    def wait_for_media_request(
        self,
        *,
        predicate,
        timeout: float,
    ) -> BrowserResolution:
        deadline = time.monotonic() + timeout
        checked = 0

        while time.monotonic() < deadline:
            while checked < len(self._responses):
                result, response_headers = self._responses[checked]
                checked += 1

                if predicate(result.media_url, response_headers):
                    return result

            if self._page is not None:
                self._page.wait_for_timeout(100)
            else:
                time.sleep(0.1)

        raise InteractionRequiredError(
            "Browser session did not observe a matching media request"
        )

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            try:
                if self._browser is not None:
                    self._browser.close()
            finally:
                if self._playwright is not None:
                    self._playwright.stop()

        self._manager = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._frame = None
        self._responses.clear()
