# 27.09.26

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from VibraVid.player.resolver import (
    CurlHttpClient,
    ManifestNotFoundError,
    Resolver,
    ResolverError,
    StaticHtmlPlayerResolver,
)
from VibraVid.utils.http_client import get_userAgent

logger = logging.getLogger(__name__)

_SUPPORTED_HOST = "vixsrc.to"


class VixSrcSource:
    """Represent a VixSrc player and its playback context.

    The class intentionally keeps player discovery separate from HLS downloading.
    A resolved manifest can be validated and handed back with the headers expected
    by VibraVid's HLS downloader.
    """

    def __init__(
        self,
        player_url: str,
        referer: str = "",
        *,
        http_client=None,
        page_resolver=None,
    ):
        self.player_url = str(player_url or "").strip()
        self.referer = str(referer or "").strip()
        self.http_client = http_client or CurlHttpClient()
        self.page_resolver = page_resolver

    @property
    def host(self) -> str:
        return (urlsplit(self.player_url).hostname or "").lower()

    def is_supported_player(self) -> bool:
        return self.host == _SUPPORTED_HOST or self.host.endswith("." + _SUPPORTED_HOST)

    def get_player_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": get_userAgent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if self.referer:
            headers["Referer"] = self.referer
        return headers

    def get_playback_headers(self) -> dict[str, str]:
        return {
            "User-Agent": get_userAgent(),
            "Referer": self.player_url,
            "Origin": "https://vixsrc.to",
        }

    @staticmethod
    def is_hls_manifest(url: str | None) -> bool:
        if not url:
            return False
        return ".m3u8" in urlsplit(str(url)).path.lower()

    def from_manifest(self, manifest_url: str | None) -> tuple[str | None, dict[str, str]]:
        """Validate an already resolved HLS manifest for this player."""
        if not self.is_supported_player():
            logger.error("Unsupported VixSrc player host: %s", self.host or "<empty>")
            return None, {}

        if not self.is_hls_manifest(manifest_url):
            return None, {}

        return str(manifest_url), self.get_playback_headers()

    def get_stream(self) -> tuple[str | None, dict[str, str]]:
        """Resolve a directly exposed HLS manifest through the shared resolver stack."""
        if not self.is_supported_player():
            logger.error("Unsupported VixSrc player host: %s", self.host or "<empty>")
            return None, {}

        page_resolver = self.page_resolver or StaticHtmlPlayerResolver(self.http_client)
        resolver = Resolver(
            self.http_client,
            page_resolver,
            user_agent=get_userAgent(),
        )

        try:
            result = resolver.resolve(self.player_url)
        except ManifestNotFoundError:
            logger.info("VixSrc player did not expose a static media manifest")
            return None, {}
        except ResolverError as error:
            logger.warning("VixSrc resolution failed: %s", error)
            return None, {}

        if not result.is_hls or not result.manifest_url:
            logger.info("VixSrc resolved source is not HLS")
            return None, {}

        return result.manifest_url, result.headers
