# 27.09.26

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
}


class ResolverError(RuntimeError):
    """Base error for player resolution failures."""


class PlayerPageError(ResolverError):
    """The player page could not be loaded or parsed."""


class InteractionRequiredError(ResolverError):
    """The player requires an interaction strategy that is not available."""


class ManifestNotFoundError(ResolverError):
    """No candidate media manifest was discovered."""


class InvalidManifestError(ResolverError):
    """A candidate manifest was found but is not a valid response."""


@dataclass(frozen=True)
class ResolvedStream:
    manifest_url: str | None
    headers: dict[str, str]
    is_hls: bool


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status_code: int
    text: str
    headers: dict[str, str]


@dataclass(frozen=True)
class BrowserResolution:
    media_url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class InteractionPlan:
    frame_selectors: tuple[str, ...] = ()
    trigger_selectors: tuple[str, ...] = ()


class HttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse:
        ...


class PlayerPageResolver(Protocol):
    def resolve_candidate(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> tuple[str, dict[str, str]]:
        ...


class BrowserSession(Protocol):
    def open(self, url: str, headers: dict[str, str]) -> None:
        ...

    def enter_frame(self, selector: str) -> None:
        ...

    def trigger(self, selector: str) -> None:
        ...

    def wait_for_media_request(
        self,
        *,
        predicate,
        timeout: float,
    ) -> BrowserResolution:
        ...

    def close(self) -> None:
        ...


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: "<redacted>" if key.lower() in _SENSITIVE_HEADERS else value
        for key, value in headers.items()
    }


class CurlHttpClient:
    """Small adapter over VibraVid's shared curl_cffi client."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse:
        with create_client(headers=headers, timeout=timeout) as client:
            response = client.get(url, headers=headers, timeout=timeout)

        return HttpResponse(
            url=str(response.url),
            status_code=response.status_code,
            text=response.text,
            headers={str(key).lower(): str(value) for key, value in response.headers.items()},
        )


class StaticHtmlPlayerResolver:
    """Resolve a media candidate exposed directly by player HTML."""

    def __init__(
        self,
        http_client: HttpClient,
        *,
        attribute: str = "data-manifest",
        timeout: float = 20.0,
    ):
        self.http_client = http_client
        self.attribute = attribute
        self.timeout = timeout

    def resolve_candidate(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> tuple[str, dict[str, str]]:
        response = self.http_client.get(url, headers=headers, timeout=self.timeout)

        if not 200 <= response.status_code < 300:
            raise PlayerPageError(
                f"Player page returned HTTP {response.status_code}: {response.url}"
            )

        candidate = self._extract_candidate(response.text, response.url)
        if not candidate:
            raise ManifestNotFoundError(
                f"No media candidate found in player page: {response.url}"
            )

        return candidate, dict(headers)

    def _extract_candidate(self, html: str, base_url: str) -> str | None:
        marker = f'{self.attribute}="'
        start = html.find(marker)
        if start < 0:
            return None

        start += len(marker)
        end = html.find('"', start)
        if end < 0:
            return None

        value = html[start:end].strip()
        if not value:
            return None

        return urljoin(base_url, value)


class BrowserEventPlayerResolver:
    """Resolve media by delegating browser interaction to an injected session."""

    def __init__(
        self,
        browser_factory,
        *,
        plan: InteractionPlan | None = None,
        timeout: float = 20.0,
    ):
        self.browser_factory = browser_factory
        self.plan = plan or InteractionPlan()
        self.timeout = timeout

    def resolve_candidate(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> tuple[str, dict[str, str]]:
        browser = self.browser_factory()

        try:
            browser.open(url, headers)

            for selector in self.plan.frame_selectors:
                browser.enter_frame(selector)

            for selector in self.plan.trigger_selectors:
                browser.trigger(selector)

            result = browser.wait_for_media_request(
                predicate=self._looks_like_manifest,
                timeout=self.timeout,
            )

            if not result.media_url:
                raise ManifestNotFoundError("Browser interaction did not expose a media URL")

            return result.media_url, dict(result.headers)
        except ResolverError:
            raise
        except Exception as error:
            raise InteractionRequiredError(
                f"Browser interaction failed: {error}"
            ) from error
        finally:
            browser.close()

    @staticmethod
    def _looks_like_manifest(url: str, headers: dict[str, str]) -> bool:
        content_type = headers.get("content-type", "").lower()
        return urlsplit(url).path.lower().endswith(".m3u8") or "mpegurl" in content_type


class Resolver:
    """Resolve a player URL into a manifest URL and playback headers."""

    def __init__(
        self,
        http_client: HttpClient,
        page_resolver: PlayerPageResolver,
        *,
        user_agent: str | None = None,
        timeout: float = 20.0,
    ):
        self.http_client = http_client
        self.page_resolver = page_resolver
        self.user_agent = user_agent or get_userAgent()
        self.timeout = timeout

    def resolve(self, url: str) -> ResolvedStream:
        request_headers = {
            "User-Agent": self.user_agent,
            "Referer": url,
        }

        logger.debug("Resolving player URL host=%s", urlsplit(url).hostname)

        try:
            candidate_url, playback_headers = self.page_resolver.resolve_candidate(
                url,
                headers=request_headers,
            )
        except ResolverError:
            raise
        except Exception as error:
            raise PlayerPageError(
                f"Unexpected error while resolving player page: {error}"
            ) from error

        headers = {
            **request_headers,
            **(playback_headers or {}),
        }

        logger.debug(
            "Resolved media candidate host=%s headers=%s",
            urlsplit(candidate_url).hostname,
            redact_headers(headers),
        )

        try:
            response = self.http_client.get(
                candidate_url,
                headers=headers,
                timeout=self.timeout,
            )
        except Exception as error:
            raise InvalidManifestError(
                f"Manifest request failed: {error}"
            ) from error

        if not 200 <= response.status_code < 300:
            raise InvalidManifestError(
                f"Manifest returned HTTP {response.status_code}: {response.url}"
            )

        is_hls = self._is_hls_manifest(
            response.text,
            response.headers,
            response.url,
        )

        if is_hls:
            logger.info(
                "Resolved HLS manifest host=%s",
                urlsplit(response.url).hostname,
            )
        else:
            logger.info(
                "Resolved player source is not HLS host=%s",
                urlsplit(response.url).hostname,
            )

        return ResolvedStream(
            manifest_url=response.url,
            headers=headers,
            is_hls=is_hls,
        )

    @staticmethod
    def _is_hls_manifest(
        body: str,
        headers: dict[str, str],
        url: str,
    ) -> bool:
        content_type = headers.get("content-type", "").lower()

        if "mpegurl" in content_type:
            return True

        if body.lstrip().startswith("#EXTM3U"):
            return True

        return urlsplit(url).path.lower().endswith(".m3u8") and body.lstrip().startswith("#EXTM3U")
