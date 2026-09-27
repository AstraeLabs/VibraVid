# 27.09.26

from VibraVid.player.browser import PlaywrightBrowserSession
from VibraVid.player.resolver import InteractionRequiredError


class FakeResponse:
    def __init__(self, url, status=200, headers=None):
        self.url = url
        self.status = status
        self.headers = headers or {}


class FakeElementHandle:
    def __init__(self, frame=None):
        self._frame = frame

    def content_frame(self):
        return self._frame


class FakeLocator:
    def __init__(self, frame, selector):
        self.frame = frame
        self.selector = selector

    def element_handle(self, timeout=10000):
        return self.frame.handles.get(self.selector)

    def wait_for(self, state="attached", timeout=10000):
        if self.selector not in self.frame.handles:
            raise RuntimeError(f"missing selector {self.selector}")

    def evaluate(self, script):
        self.frame.events.append(("trigger", self.selector))


class FakeFrame:
    def __init__(self):
        self.handles = {}
        self.events = []

    def locator(self, selector):
        return FakeLocator(self, selector)


class FakePage(FakeFrame):
    def __init__(self):
        super().__init__()
        self.handlers = {}
        self.goto_response = FakeResponse(
            "https://example.test/player",
            200,
            {"content-type": "text/html"},
        )

    def on(self, event, callback):
        self.handlers[event] = callback

    def goto(self, url, wait_until, timeout):
        self.events.append(("goto", url))
        return self.goto_response

    def wait_for_timeout(self, milliseconds):
        return None

    def emit_response(self, response):
        self.handlers["response"](response)


class FakeContext:
    def __init__(self, page):
        self.page = page
        self.headers = None
        self.closed = False

    def new_page(self):
        return self.page

    def set_extra_http_headers(self, headers):
        self.headers = headers

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.closed = False

    def new_context(self, **kwargs):
        return self.context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    def launch(self, **kwargs):
        return self.browser


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium


class FakeManager:
    def __init__(self, playwright):
        self.playwright = playwright
        self.stopped = False

    def start(self):
        return self.playwright

    def stop(self):
        self.stopped = True


def make_session():
    page = FakePage()
    context = FakeContext(page)
    browser = FakeBrowser(context)
    manager = FakeManager(FakePlaywright(FakeChromium(browser)))

    session = PlaywrightBrowserSession(
        playwright_factory=lambda: manager,
    )
    return session, page, context, browser, manager


def test_browser_session_opens_page_and_applies_headers():
    session, page, context, browser, manager = make_session()

    session.open(
        "https://example.test/player",
        {"User-Agent": "BrowserTest/1.0"},
    )

    assert context.headers == {"User-Agent": "BrowserTest/1.0"}
    assert page.events == [("goto", "https://example.test/player")]

    session.close()

    assert context.closed is True
    assert browser.closed is True
    assert manager.stopped is True


def test_browser_session_enters_frame_and_triggers_selector():
    session, page, _context, _browser, _manager = make_session()
    child = FakeFrame()
    page.handles["iframe.player"] = FakeElementHandle(child)
    child.handles["#play"] = FakeElementHandle()

    session.open("https://example.test/player", {})
    session.enter_frame("iframe.player")
    session.trigger("#play")

    assert child.events == [("trigger", "#play")]
    session.close()


def test_browser_session_returns_matching_media_response():
    session, page, _context, _browser, _manager = make_session()
    session.open("https://example.test/player", {})

    page.emit_response(
        FakeResponse(
            "https://cdn.example.test/master.m3u8",
            200,
            {"content-type": "application/vnd.apple.mpegurl"},
        )
    )

    result = session.wait_for_media_request(
        predicate=lambda url, headers: url.endswith(".m3u8"),
        timeout=0.1,
    )

    assert result.media_url == "https://cdn.example.test/master.m3u8"
    assert result.headers["content-type"] == "application/vnd.apple.mpegurl"
    session.close()


def test_browser_session_fails_cleanly_when_navigation_has_no_response():
    session, page, _context, _browser, _manager = make_session()
    page.goto_response = None

    try:
        session.open("https://example.test/player", {})
    except InteractionRequiredError as error:
        assert "did not return an HTTP response" in str(error)
    else:
        raise AssertionError("expected InteractionRequiredError")

    session.close()
