# 29.01.26

import logging
import threading

from rich.console import Console

from VibraVid.utils.config import config_manager
from VibraVid.utils.http_client import create_client
from VibraVid.utils.vault._url_utils import clean_license_url

console = Console()
logger = logging.getLogger(__name__)
db_config = config_manager.config.get_dict("DRM", "vault")
VAULT_URL = db_config.get("vault_1", {}).get("url", "")
TOKEN = db_config.get("vault_1", {}).get("token", "")


class ExternalSupaDBVault:
    def __init__(self, *, name: str, base_url: str | None = None, token: str | None = None):
        self.name = name
        self.base_url = base_url if base_url is not None else VAULT_URL
        token = token if token is not None else TOKEN
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

        self.session = create_client(headers=self.headers, http2=True)
        self._session_lock = threading.Lock()
        self._prewarm()

    @property
    def is_connected(self) -> bool:
        return bool(self.base_url)

    def _prewarm(self) -> None:
        """Open the TLS connection in a background thread so the first real lookup doesn't pay the handshake."""
        if not self.base_url:
            return

        def _warm():
            try:
                with self._session_lock:
                    self.session.get(self.base_url, timeout=10)
                logger.debug("Supa vault connection prewarmed")
            except Exception as e:
                logger.debug(f"Supa vault prewarm skipped (non-fatal): {e}")

        threading.Thread(target=_warm, daemon=True, name="supa-vault-prewarm").start()

    def close(self):
        """Close the HTTP session."""
        if self.session:
            self.session.close()

    def _clean_license_url(self, license_url: str) -> str:
        return clean_license_url(license_url)

    def _post(self, endpoint: str, payload: dict) -> dict | None:
        """Internal helper: POST to an endpoint, return parsed JSON or None on error."""
        url = f"{self.base_url}/{endpoint}"
        try:
            logger.debug(f"Post to Claudio endpoint '{endpoint}' with payload: {payload}")
            with self._session_lock:
                response = self.session.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            console.print(f"[red]Claudio request error ({endpoint}): {e}")
            logger.error(f"Claudio request error ({endpoint}): {e}")
            return None

    def track_download(self, title: str, media_type: str, service: str = None) -> bool:
        """Notify Claudio about a completed download."""
        if not title or not media_type:
            return False

        payload = {
            "service": (service or "").strip().lower(),
            "type": media_type.strip().lower(),
            "title": title.strip(),
        }
        logger.debug(f"Tracking download with payload: {payload}")

        url = f"{self.base_url}/track-downloads"
        try:
            with self._session_lock:
                response = self.session.post(url, json=payload)
            response.raise_for_status()
            result = response.json()

            return bool(result.get("success", False))

        except Exception as e:
            logger.error(f"Claudio track_download error: {e}")
            return False

    def set_keys(self, keys_list: list[str], license_url: str, pssh: str, kid_to_label: dict | None = None) -> int:
        """
        Add multiple keys to the vault in a single bulk request.

        Returns:
            int: Number of keys successfully added
        """
        if not keys_list:
            return 0

        base_license_url = self._clean_license_url(license_url)
        keys_payload = []
        for key_str in keys_list:
            if ":" not in key_str:
                continue

            kid, key = key_str.split(":", 1)
            kid_clean = kid.strip()
            kid_norm = kid_clean.lower().replace("-", "")
            entry: dict = {"kid": kid_clean, "key": key.strip()}

            if kid_to_label:
                label = kid_to_label.get(kid_norm)
                if label:
                    entry["label"] = label

            keys_payload.append(entry)

        if not keys_payload:
            return 0

        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
            "keys": keys_payload,
        }

        result = self._post("save-keys", payload)
        logger.debug(f"Vault response for saving keys: {result}")

        if result is None:
            return 0

        added = result.get("added", 0)
        return added

    def get_keys_by_pssh(self, license_url: str, pssh: str) -> list[str]:
        """
        Retrieve all keys for a given license URL and PSSH (single request).

        Returns:
            List[str]: List of "kid:key" strings
        """
        base_license_url = self._clean_license_url(license_url)
        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
        }

        logger.debug(f"Claudio get_keys_by_pssh: license_url={base_license_url}, pssh={pssh[:20]}...")
        result = self._post("get-keys", payload)
        logger.debug(f"Vault response for get_keys_by_pssh: {result}")

        if result is None:
            return []

        return [k["kid_key"] for k in result.get("keys", [])]

    def get_keys_by_kids(self, license_url: str | None, kids: list[str], pssh: str = None) -> list[str]:
        """
        Retrieve keys for one or more KIDs in a single bulk request.
        If license_url is None the search is global.

        Returns:
            List[str]: List of "kid:key" strings
        """
        if not kids:
            return []

        normalized_kids = [k.replace("-", "").strip().lower() for k in kids]
        base_license_url = self._clean_license_url(license_url) if license_url else None

        payload: dict = {"kids": normalized_kids}
        if base_license_url:
            payload["license_url"] = base_license_url

        result = self._post("get-keys", payload)
        logger.debug(f"Vault response for get_keys_by_kids: {result}")

        if result is None:
            return []

        return [k["kid_key"] for k in result.get("keys", [])]

    def get_keys_by_kid(self, license_url: str | None, kid: str) -> list[str]:
        """Convenience wrapper for a single KID lookup."""
        return self.get_keys_by_kids(license_url, [kid])


claudio_vault = ExternalSupaDBVault(name="claudio")
