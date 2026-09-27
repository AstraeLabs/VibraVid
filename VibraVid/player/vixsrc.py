# 27.09.26

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from VibraVid.utils.http_client import get_userAgent

logger = logging.getLogger(__name__)

_SUPPORTED_HOST = "vixsrc.to"


class VixSrcSource:
    """Represent a VixSrc player and its playback context.

    The class intentionally keeps player discovery separate from HLS downloading.
    A resolved manifest can be validated and handed back with the headers expected
    by VibraVid's HLS downloader.
    """

    def __init__(self, player_url: str, referer: str = ""):
        self.player_url = str(player_url or "").strip()
        self.referer = str(referer or "").strip()

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
        """Return a resolved HLS stream when one is available.

        Player interaction and network interception are intentionally not performed
        here. The provider remains able to detect the player and fail cleanly until
        a supported manifest discovery strategy is supplied.
        """
        if not self.is_supported_player():
            logger.error("Unsupported VixSrc player host: %s", self.host or "<empty>")
            return None, {}

        logger.info("VixSrc player detected but no manifest discovery strategy is configured")
        return None, {}
