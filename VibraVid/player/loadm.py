# 29.05.26
# By @UrloMythus

import json
import logging
import re

from Cryptodome.Cipher import AES

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

_KEY = b"kiemtienmua911ca"
_IV = b"1234567890oiuytr"


def _hex_to_bytes(hex_str: str) -> bytes:
    hex_str = re.sub(r"[^0-9a-fA-F]", "", hex_str)
    if len(hex_str) % 2 != 0:
        hex_str = "0" + hex_str
    return bytes(int(hex_str[i : i + 2], 16) for i in range(0, len(hex_str), 2))


def _aes_cbc_decrypt(ciphertext: bytes) -> bytes:
    cipher = AES.new(_KEY, AES.MODE_CBC, _IV)
    decrypted = cipher.decrypt(ciphertext)
    pad_len = decrypted[-1]
    if 1 <= pad_len <= 16:
        decrypted = decrypted[:-pad_len]
    return decrypted


class LoadmSource:
    def __init__(self, player_link: str, referer: str = ""):
        parts = player_link.split("#", 1)
        self.player_url = parts[0].rstrip("/") + "/"
        self.video_id = parts[1] if len(parts) > 1 else ""
        self.referer = referer

    def get_stream(self) -> tuple[str | None, dict]:
        """
        Returns (stream_url, playback_headers) or (None, {}) on failure.
        """
        headers = {
            "User-Agent": get_userAgent(),
            "Referer": self.player_url,
        }
        params = {
            "id": self.video_id,
            "w": "2560",
            "h": "1440",
            "r": self.referer,
        }
        api_url = self.player_url + "api/v1/video"
        client = create_client(headers=headers)

        try:
            response = client.get(api_url, params=params)
            response.raise_for_status()
            ciphertext = _hex_to_bytes(response.text)
            data = json.loads(_aes_cbc_decrypt(ciphertext).decode("utf-8"))

        except Exception as e:
            logger.error(f"[Loadm] Failed to get stream for id={self.video_id!r}: {e}")
            return None, {}

        finally:
            client.close()

        url = data.get("cf") or data.get("source")
        return url, {"Referer": self.player_url}
