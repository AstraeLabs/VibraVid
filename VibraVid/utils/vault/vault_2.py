# 29.01.26

import logging
import threading
from base64 import b64decode

from rich.console import Console

from VibraVid.utils.config import config_manager
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils.vault.base import BaseVault

console = Console()
logger = logging.getLogger(__name__)
db_config = config_manager.config.get_dict("DRM", "vault")
VAULT_URL = db_config.get("vault_2", {}).get("url", "")
TOKEN = db_config.get("vault_2", {}).get("token", "")


def _extract_kid_from_pssh(pssh_b64: str) -> str | None:
    """Extract KID hex string from a PlayReady PSSH base64 blob."""
    try:
        data = b64decode(pssh_b64)
    except Exception:
        return None

    if b"<KID>" in data:
        start = data.index(b"<KID>") + 5
        end = data.index(b"</KID>", start)
        try:
            return b64decode(data[start:end]).hex()
        except Exception:
            return None

    return None


class Vault2Client(BaseVault):
    def __init__(self, *, name: str):
        super().__init__(name=name)
        self.session = create_client(headers=get_headers())
        self._session_lock = threading.Lock()
        self._prewarm(VAULT_URL, self._session_lock, "Lab")

    @property
    def is_connected(self) -> bool:
        return bool(VAULT_URL and TOKEN)

    def _api_call(self, method: str, params: dict) -> dict:
        """POST a JSON-RPC-style request to the lab vault, return the `message` dict."""
        payload = {"method": method, "params": params, "token": TOKEN}
        try:
            logger.debug(f"Calling Lab API ({method}): {params}")
            with self._session_lock:
                r = self.session.post(VAULT_URL, json=payload)
            r.raise_for_status()
            data = r.json()

            if data.get("status_code") != 200:
                raise RuntimeError(f"Lab API error: {data}")
            return data.get("message", {})

        except Exception as e:
            logger.error(f"Lab API call failed ({method}): {e}")
            console.print(f"[red]Lab API call failed ({method}): {e}")
            return {}

    def _normalize_kid(self, kid: str) -> str:
        """Return a clean lowercase hex KID, resolving PSSH blobs when needed."""
        if "=" in kid and len(kid) > 32:
            resolved = _extract_kid_from_pssh(kid)
            if resolved:
                return resolved
        return kid.replace("-", "").strip().lower()

    def set_key(self, kid: str, key: str, license_url: str, pssh: str = None, label: str = None) -> bool:
        """Store a single DRM key in vault."""
        pass

    def get_keys_by_kids(self, license_url: str | None, kids: list[str], pssh: str = None) -> list[str]:
        """Retrieve keys for one or more KIDs"""
        if not kids:
            return []

        results: list[str] = []

        for kid_raw in kids:
            kid = self._normalize_kid(kid_raw)
            params: dict = {"kid": kid, "session_id": None}
            if license_url:
                params["service"] = self._clean_license_url(license_url)

            msg = self._api_call("GetKey", params)
            if not msg:
                continue

            for entry in msg.get("keys", []):
                if isinstance(entry, dict):
                    if entry.get("kid") == kid:
                        key_val = entry.get("key")
                        if key_val:
                            results.append(f"{kid}:{key_val}")
                elif isinstance(entry, str) and ":" in entry:
                    k, v = entry.split(":", 1)
                    if k == kid:
                        results.append(f"{kid}:{v}")

        return results


lab_vault = Vault2Client(name="lab")
