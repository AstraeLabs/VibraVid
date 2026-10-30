# 30.10.26

import base64
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlparse

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_userAgent, get_with_retry

logger = logging.getLogger(__name__)
_ENV = "1920x1080:it-IT:UTF-8"


@dataclass
class ResolvedStream:
    url: str
    type: str  # "hls" | "mp4"
    headers: dict = field(default_factory=dict)


class LoonexClient:
    def __init__(self):
        self.base_url = self._base_url()
        self.headers = {
            "User-Agent": get_userAgent(),
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
            "Referer": f"{self.base_url}/",
        }

    @staticmethod
    def _base_url() -> str:
        """Resolve the current Loonex domain from the shared domain config."""
        return config_manager.domain.get("loonex", "full_url").rstrip("/")

    def get_html(self, url: str, params: dict | None = None) -> str:
        with create_client(headers=self.headers, follow_redirects=True) as client:
            response = get_with_retry(client, url, params=params)
        return response.text

    @staticmethod
    def _link_key(seed: int) -> list[int]:
        h = (seed * 0x01000193) & 0xFFFFFFFF
        key = []
        for i in range(32):
            h = ((h ^ ((i * 109 + 89) & 0xFFFFFFFF)) * 0x01000193) & 0xFFFFFFFF
            key.append((h >> ((i % 4) * 8)) & 0xFF)
        return key

    @classmethod
    def _decode_link_with_seed(cls, value: str, seed: int) -> str:
        try:
            raw = base64.b64decode(value)
        except Exception:
            return ""
        key = cls._link_key(seed)
        return "".join(chr(b ^ key[i % 32] ^ ((i * 7 + 13) & 0xFF)) for i, b in enumerate(raw))

    @staticmethod
    def is_valid_link(link: str) -> bool:
        """True for a plausible decoded URL/path (printable ASCII, absolute or site-relative)."""
        return len(link) >= 8 and (link[0] == "/" or link.startswith("http")) and all(33 <= ord(c) <= 126 for c in link)

    def decode_link(self, value: str, seed: int = 50) -> str | None:
        """Decode a ``data-stream`` value; the page tries its own seed first, then scans every seed like the player does."""
        for candidate in [seed, *range(17, 90), *range(1, 129)]:
            link = self._decode_link_with_seed(value, candidate)
            if self.is_valid_link(link):
                return link if link.startswith("http") else f"{self.base_url}{link}"
        return None

    @staticmethod
    def _rc4(data: bytes, key: bytes) -> bytes:
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) & 255
            s[i], s[j] = s[j], s[i]

        i = j = 0
        out = bytearray()
        for byte in data:
            i = (i + 1) & 255
            j = (j + s[i]) & 255
            s[i], s[j] = s[j], s[i]
            out.append(byte ^ s[(s[i] + s[j]) & 255])
        return bytes(out)

    @classmethod
    def _unpack_payload(cls, payload: str, key: str) -> dict | None:
        """Decrypt an ``LX2``/``LX3`` RC4 envelope (url-safe base64) into its JSON body."""
        b64 = payload.replace("-", "+").replace("_", "/")
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
        for candidate in (key, f"{key}:lx3"):
            plain = cls._rc4(raw, candidate.encode()).decode("latin-1")
            if plain.startswith(("LX2:", "LX3:")):
                try:
                    return json.loads(unquote(plain[4:]))
                except ValueError:
                    return None
        return None

    @staticmethod
    def _sha256(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _js_const(html: str, name: str) -> str | None:
        """Read ``const <name> = "<value>"`` (optionally wrapped in a returning IIFE) from the player page."""
        match = re.search(rf"const\s+{name}\s*=\s*(?:\(function\(\)\s*\{{\s*return\s*)?[\"']([^\"']+)[\"']", html)
        return match.group(1) if match else None

    @staticmethod
    def _is_flag_set(html: str, name: str) -> bool:
        return re.search(rf"const\s+{name}\s*=\s*true", html) is not None

    def _player_type(self, html: str) -> str:
        """Same precedence as the page's own ``player_type`` expression."""
        if self._is_flag_set(html, "isOkru"):
            return "zk_id"
        if self._is_flag_set(html, "isOkruBypass"):
            return "zk_bp_id"
        if self._is_flag_set(html, "isRumble"):
            return "rmb_id"
        if self._is_flag_set(html, "isDrive"):
            return "driv"
        if self._is_flag_set(html, "isDrime"):
            return "drim"
        return "norm"

    def resolve_stream(self, play_url: str) -> ResolvedStream | None:
        """Resolve a decoded play URL (``index_ext`` Rumble page or ``guarda.php`` player page) to a downloadable stream."""
        with create_client(headers=self.headers, follow_redirects=True) as client:
            html = get_with_retry(client, play_url).text

            if "/index_ext" in play_url:
                return self._resolve_rumble(client, html)

            if self._is_flag_set(html, "isDrime"):
                return self._resolve_drime(client, play_url, html)

            return self._resolve_authorized(client, play_url, html)

    def _loonex_headers(self) -> dict:
        """Headers for videoserver"""
        return {
            "User-Agent": self.headers["User-Agent"],
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    def _resolve_authorized(self, client, play_url: str, html: str) -> ResolvedStream | None:
        """Replicate ``guardaResolveAuthorizedStream``: sha256 proof-of-environment, then an RC4-wrapped JSON answer."""
        # _lxSecConfig is a flat JSON object (the site serialises it with json_encode, not as a bare JS literal) — parse it directly
        # rather than pulling each field out with its own regex, which silently matched the wrong "ts:" elsewhere on the page once
        # the site started quoting its keys (`"ch":"..."` instead of `ch: "..."`).
        config_match = re.search(r"_lxSecConfig\s*=\s*(\{[^}]*\})", html)
        try:
            sec_config = json.loads(config_match.group(1)) if config_match else {}
        except ValueError:
            sec_config = {}
        ch, tok, act, ts, nonce = sec_config.get("ch"), sec_config.get("tok"), sec_config.get("act"), sec_config.get("ts"), sec_config.get("nonce")
        video_id = self._js_const(html, "currentVideoIdSanitized") or self._js_const(html, "currentVideoId")
        raw_video_id = self._js_const(html, "currentVideoId") or video_id
        if not (ch and tok and act and ts and nonce and video_id):
            logger.error(f"Loonex: player config not found in {play_url}")
            return None

        proof = self._sha256(f"{ch}:{video_id}:{ts}:{_ENV}")
        response_key = self._sha256(f"{ch}:resp:{proof}")

        response = client.post(
            play_url,
            data={
                "action": act, "tok": tok, "poe_ch": ch, "poe_ts": str(ts), "poe_proof": proof, "env": _ENV,
                "video_id": video_id, "raw_video_id": raw_video_id, "player_type": self._player_type(html), "srv": "1",
            },
            headers={
                "X-Requested-With": "XMLHttpRequest", "Origin": self.base_url, "Referer": play_url,
                "Accept": "application/json, text/plain, */*", "X-Loonex-Playback": nonce,
            },
        )
        body = response.json()
        data = self._unpack_payload(body["payload"], response_key) if body.get("payload") else body
        if not data or not data.get("ok"):
            logger.error(f"Loonex: stream authorization refused for {play_url}")
            return None

        okru = data.get("okruData") or {}
        if okru.get("hls"):
            return ResolvedStream(okru["hls"], "hls", {"User-Agent": self.headers["User-Agent"]})

        stream_url = data.get("streamUrl") or ""
        if not stream_url or "start1.mp4" in stream_url:
            logger.error(f"Loonex: no real stream for {play_url} (decoy or empty answer)")
            return None

        is_hls = ".m3u8" in urlparse(stream_url).path.lower()
        headers = self._loonex_headers() if urlparse(stream_url).netloc.endswith(urlparse(self.base_url).netloc.removeprefix("www.")) else {"User-Agent": self.headers["User-Agent"]}
        return ResolvedStream(stream_url, "hls" if is_hls else "mp4", headers)

    def _resolve_drime(self, client, play_url: str, html: str) -> ResolvedStream | None:
        """Drime titles resolve through ``drime_resolve`` (the generic authorization answers with a decoy)."""
        drime_hash = re.search(r"const\s+guardaDrimeHash\s*=\s*[\"']([^\"']+)[\"']", html)
        if not drime_hash:
            logger.error(f"Loonex: Drime hash not found in {play_url}")
            return None

        response = client.post(
            play_url,
            data={"action": "drime_resolve", "hash": drime_hash.group(1)},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": play_url, "Accept": "application/json, text/plain, */*"},
        )
        stream_url = (response.json() or {}).get("stream")
        if not stream_url:
            logger.error(f"Loonex: Drime did not return a stream for {play_url}")
            return None

        is_hls = ".m3u8" in urlparse(stream_url).path.lower()
        return ResolvedStream(stream_url, "hls" if is_hls else "mp4", {"User-Agent": self.headers["User-Agent"]})

    def _resolve_rumble(self, client, html: str) -> ResolvedStream | None:
        """``index_ext`` pages embed a Rumble video whose public embed API lists an HLS playlist."""
        video = re.search(r"Rumble\(\s*[\"']play[\"']\s*,\s*\{\s*[\"']video[\"']\s*:\s*[\"']([A-Za-z0-9]+)[\"']", html)
        if not video:
            logger.error("Loonex: Rumble video id not found")
            return None

        info = get_with_retry(client, "https://rumble.com/embedJS/u3/", params={"request": "video", "ver": "2", "v": video.group(1)}).json()
        sources = info.get("ua") or info.get("u") or {}
        hls = (sources.get("hls") or {}).get("auto", {}).get("url")
        headers = {"User-Agent": self.headers["User-Agent"]}
        if hls:
            return ResolvedStream(hls, "hls", headers)

        mp4 = sources.get("mp4") or {}
        if mp4:
            best = max(mp4, key=lambda quality: int(quality) if str(quality).isdigit() else 0)
            return ResolvedStream(mp4[best]["url"], "mp4", headers)

        logger.error(f"Loonex: Rumble returned no playable source for {video.group(1)}")
        return None
