# 29.01.26

import logging
import threading

from rich.console import Console

from VibraVid.utils.config import config_manager
from VibraVid.utils.http_client import create_client
from VibraVid.utils.upload.version import __version__
from VibraVid.utils.vault.base import BaseVault

console = Console()
logger = logging.getLogger(__name__)
db_config = config_manager.config.get_dict("DRM", "vault")
VAULT_URL = db_config.get("vault_1", {}).get("url", "")
TOKEN = db_config.get("vault_1", {}).get("token", "")


class Vault1Client(BaseVault):
    def __init__(self, *, name: str, base_url: str | None = None, token: str | None = None):
        super().__init__(name=name)
        self.base_url = base_url if base_url is not None else VAULT_URL
        token = token if token is not None else TOKEN
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

        self.session = create_client(headers=self.headers, http2=True)
        self._session_lock = threading.Lock()
        self._prewarm(self.base_url, self._session_lock, "Claudio")

    @property
    def is_connected(self) -> bool:
        return bool(self.base_url)

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
            "app_version": __version__,
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

    def track_download_async(self, title: str, media_type: str, service: str = None) -> None:
        """Fire-and-forget: notify Claudio about a completed download in a background thread."""
        def _run():
            try:
                if not self.is_connected:
                    logger.warning("track_download_async: claudio_vault not configured")
                    return

                title_str = (title or "").strip()
                media_type_str = (media_type or "Film").strip()
                service_str = (service or "").strip().lower()
                logger.debug(f"[TRACK] Tracking download: title={title_str}, type={media_type_str}, service={service_str}")
                result = self.track_download(title=title_str, media_type=media_type_str, service=service_str)
                logger.debug(f"[TRACK] Track result: {result}")
            except Exception as e:
                logger.error(f"[TRACK] Error tracking download: {e}", exc_info=True)

        threading.Thread(target=_run, daemon=False).start()

    def set_keys(self, keys_list: list[str], license_url: str, pssh: str, kid_to_label: dict | None = None) -> int:
        """Store a list of keys in the vault. Returns the number of keys successfully stored."""
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
            "app_version": __version__,
        }

        result = self._post("save-keys", payload)
        logger.debug(f"Vault response for saving keys: {result}")

        if result is None:
            return 0

        added = result.get("added", 0)
        return added

    def get_keys_by_pssh(self, license_url: str, pssh: str) -> list[str]:
        """Retrieve keys for a given PSSH and license URL."""
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

        keys = result.get("keys", [])
        self._flag_keys(keys, base_license_url)
        return [k["kid_key"] for k in keys]

    def get_keys_by_kids(self, license_url: str | None, kids: list[str], pssh: str = None) -> list[str]:
        """Retrieve keys for a list of KIDs, optionally filtered by license URL and PSSH."""
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

        keys = result.get("keys", [])
        self._flag_keys(keys, base_license_url)

        return [k["kid_key"] for k in keys]

    def _flag_keys(self, keys: list[dict], requested_license_url: str | None) -> None:
        """Check returned keys for any warnings or mismatches, and log them."""
        self.last_key_flags = {}
        requested_is_real_url = bool(requested_license_url) and requested_license_url.lower().startswith("http")

        for k in keys:
            kid = str(k.get("kid") or "").lower()
            kid_key = f"{k.get('kid')}:{k.get('key')}"
            tags: list[str] = []

            returned_license_url = k.get("license_url")
            if returned_license_url and not returned_license_url.lower().startswith("http"):
                tags.append("GENERIC")
                self._log_flag(kid_key, "GENERIC", "stored without a real license_url -- resolved outside any license context")
            elif requested_is_real_url and returned_license_url and returned_license_url != requested_license_url:
                tags.append("LICENSE_MISMATCH")
                self._log_flag(kid_key, "LICENSE_MISMATCH", f"expected {requested_license_url!r}, got {returned_license_url!r}")

            warning_count = k.get("warning_count") or 0
            if warning_count > 0:
                tag = f"WARN: {warning_count}"
                tags.append(tag)
                self._log_flag(kid_key, tag, "previously reported as a wrong key -- verify before trusting it")

            if tags and kid:
                self.last_key_flags[kid] = ", ".join(tags)

    def _log_flag(self, kid_key: str, tag: str, detail: str) -> None:
        logger.warning(f"[{self.name}] {tag}: {kid_key} — {detail}")

    def report_wrong_key(self, kid: str, key: str, license_url: str, pssh: str | None = None) -> bool:
        """Report a wrong key to the vault. Returns True if the vault acknowledged the report."""
        base_license_url = self._clean_license_url(license_url) if license_url else None
        if not base_license_url:
            return False

        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
            "kid": kid,
            "key": key,
            "invalid": True,
        }
        result = self._post("save-keys", payload)
        logger.debug(f"Vault response for report_wrong_key: {result}")
        return bool(result and result.get("warnings_recorded", 0) > 0)


claudio_vault = Vault1Client(name="claudio")
