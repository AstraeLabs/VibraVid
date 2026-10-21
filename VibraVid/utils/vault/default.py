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
_default_cfg = db_config.get("default") or db_config.get("vault_1") or {}
VAULT_URL = _default_cfg.get("url", "")
TOKEN = _default_cfg.get("token", "")


class Vault_di_default(BaseVault):
    def __init__(self, *, name: str, base_url: str | None = None, token: str | None = None):
        super().__init__(name=name)
        self.base_url = base_url if base_url is not None else VAULT_URL
        token = token if token is not None else TOKEN
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

        self.session = create_client(headers=self.headers, http2=True)
        self._session_lock = threading.Lock()
        self._warm_lock = threading.Lock()
        self._warm_started = False
        self.last_origins: dict[str, tuple[str, str | None, str | None]] = {}
        self.warm()

    def warm(self) -> None:
        """Wake the Worker and the Postgres behind it before the first lookup. Idempotent."""
        with self._warm_lock:
            if self._warm_started:
                return
            self._warm_started = True
        self._prewarm(self.base_url, self._session_lock, "Claudio", path="health", timeout=5)

    @property
    def is_connected(self) -> bool:
        return bool(self.base_url)

    @property
    def is_writable(self) -> bool:
        return True

    def _post(self, endpoint: str, payload: dict) -> dict | None:
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
        def _run():
            try:
                if not self.is_connected:
                    logger.warning("track_download_async: default_vault not configured")
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
        if not keys_list:
            return 0

        base_license_url = self._clean_license_url(license_url)
        keys_payload = []
        for key_str in keys_list:
            if ":" not in key_str:
                continue

            kid, key = key_str.split(":", 1)
            keys_payload.append({"kid": kid.strip(), "key": key.strip()})

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

        locked = result.get("locked", 0)
        if locked:
            logger.warning(f"[{self.name}] {locked} key(s) not stored: a different key is already saved for the same KID")

        added = result.get("added", 0)
        return added

    def get_keys_by_pssh(self, license_url: str, pssh: str) -> list[str]:
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

            if kid and k.get("key"):
                self.last_origins[kid] = (str(k["key"]).lower(), k.get("license_url"), k.get("pssh"))

    def _log_flag(self, kid_key: str, tag: str, detail: str) -> None:
        logger.warning(f"[{self.name}] {tag}: {kid_key} — {detail}")

    def _row_for(self, kid: str, key: str, license_url: str | None, pssh: str | None) -> tuple[str | None, str | None]:
        """(license_url, pssh) of the vault row holding *key* for *kid* when this vault returned it, else the ones given."""
        origin = self.last_origins.get(kid.lower())
        if origin and origin[0] == key.lower() and origin[1]:
            return origin[1], origin[2]
        return (self._clean_license_url(license_url) if license_url else None), pssh

    def report_wrong_key(self, kid: str, key: str, license_url: str, pssh: str | None = None) -> bool:
        base_license_url, pssh = self._row_for(kid, key, license_url, pssh)
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

    def replace_key(self, kid: str, new_key: str, old_key: str, license_url: str, pssh: str | None = None) -> bool:
        """Swap *old_key* (already reported wrong) for *new_key* on its row; a vault with no entry for the KID just stores it."""
        base_license_url, pssh = self._row_for(kid, old_key, license_url, pssh)
        if not base_license_url:
            return False

        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
            "kid": kid,
            "key": new_key,
            "replaces": old_key,
            "app_version": __version__,
        }
        result = self._post("save-keys", payload)
        logger.debug(f"Vault response for replace_key: {result}")
        if result is None:
            return False

        if result.get("locked"):
            if "replaced" not in result:
                logger.warning(f"[{self.name}] key for KID {kid} not replaced: the vault server is too old to replace keys (deploy the updated worker)")
            else:
                logger.warning(f"[{self.name}] key for KID {kid} not replaced: the saved key is not the reported one (already fixed by someone else?)")
        return bool(result.get("replaced") or result.get("added") or result.get("unchanged"))


default_vault = Vault_di_default(name="claudio")
