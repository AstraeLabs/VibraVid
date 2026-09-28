# 21.03.25
# By @UrloMythus

import base64
import logging
import re

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)
VIDXGO_HEADERS = {
    "User-Agent": get_userAgent(),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Alt-Used": "v.vidxgo.co",
    "Sec-Fetch-Dest": "iframe",
}

PLAYBACK_HEADERS = {
    "User-Agent": get_userAgent(),
    "Referer": "https://v.vidxgo.co/",
    "Origin": "https://v.vidxgo.co",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
}

_OBFUSCATED_RE = re.compile(r"var\s+\w+\s*=\s*'(\w+)'\s*,?\s*d\s*=\s*atob\s*\(\s*'([A-Za-z0-9+/=]+)'\s*\)", re.S)
_CURRENT_SRC_RE = re.compile(r'currentSrc[^"]+"(https:[^";]+)', re.S)


class VideoSource:
    def __init__(
        self,
        imdb_id: str,
        season_number: int = None,
        episode_number: int = None,
        embed_domain: str = "https://v.vidxgo.co",
        content_type: str = "series",
        referer: str | None = None,
    ):
        self.imdb_id = str(imdb_id).strip()
        if self.imdb_id.startswith("tt"):
            self.imdb_id = self.imdb_id[2:]

        self.season_number = season_number
        self.episode_number = episode_number
        self.embed_domain = embed_domain.rstrip("/")
        self.content_type = content_type
        self.referer = referer

    @staticmethod
    def decode_embed_html(html_text: str) -> str | None:
        xor_patterns = _OBFUSCATED_RE.findall(html_text)
        if not xor_patterns:
            return None

        for key, b64_payload in xor_patterns:
            try:
                decoded = base64.b64decode(b64_payload)
            except Exception:
                continue

            if not key:
                continue

            key_bytes = key.encode("utf-8")
            key_len = len(key_bytes)
            payload = bytearray(len(decoded))

            for index, byte_value in enumerate(decoded):
                payload[index] = byte_value ^ key_bytes[index % key_len]

            decrypted = payload.decode("utf-8", errors="ignore")
            match = _CURRENT_SRC_RE.search(decrypted)
            if match:
                return match.group(1).replace("\\", "")

        return None

    def get_playlist(self) -> str | None:
        if not self.imdb_id:
            logger.error("VidXgo requires an IMDb ID")
            return None

        if self.content_type == "movie":
            embed_url = f"{self.embed_domain}/tt{self.imdb_id}"
        else:
            embed_url = f"{self.embed_domain}/tt{self.imdb_id}/{self.season_number}/{self.episode_number}"

        headers = VIDXGO_HEADERS.copy()
        if self.referer:
            headers["Referer"] = self.referer

        try:
            with create_client() as client:
                response = client.get(embed_url, headers=headers, timeout=30)
            response.raise_for_status()
            return self.decode_embed_html(response.text)
        except Exception as error:
            logger.error(f"VidXgo decode error for {embed_url}: {error}")

        return None

    def get_playback_headers(self) -> dict:
        return PLAYBACK_HEADERS.copy()
