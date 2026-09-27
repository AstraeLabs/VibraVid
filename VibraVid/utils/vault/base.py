# 25.09.26

import logging
import threading

from VibraVid.utils.vault._url_utils import clean_license_url

logger = logging.getLogger(__name__)


class BaseVault:
    session = None

    def __init__(self, *, name: str):
        self.name = name
        self.last_key_flags: dict[str, str] = {}

    @property
    def is_connected(self) -> bool:
        return False

    def close(self) -> None:
        """Close the HTTP session, if one was opened."""
        if self.session:
            self.session.close()

    def _clean_license_url(self, license_url: str) -> str:
        return clean_license_url(license_url)

    def track_download_async(self, title: str, media_type: str, service: str = None) -> None:
        """No-op by default: not every vault tracks completed downloads."""
        pass

    def set_keys(self, keys_list: list[str], license_url: str, pssh: str = None, kid_to_label: dict | None = None) -> int:
        """No-op by default. Returns the number of keys successfully stored."""
        return 0

    def get_keys_by_pssh(self, license_url: str, pssh: str) -> list[str]:
        """No-op by default. Returns a list of "kid:key" strings."""
        return []

    def get_keys_by_kids(self, license_url: str | None, kids: list[str], pssh: str = None) -> list[str]:
        """No-op by default. Returns a list of "kid:key" strings."""
        return []

    def get_keys_by_kid(self, license_url: str | None, kid: str) -> list[str]:
        """Convenience wrapper for a single KID lookup."""
        return self.get_keys_by_kids(license_url, [kid])

    def report_wrong_key(self, kid: str, key: str, license_url: str, pssh: str | None = None) -> bool:
        """No-op by default: not every vault backend has a wrong-key/warning concept."""
        return False

    def _prewarm(self, url: str, session_lock: threading.Lock, label: str) -> None:
        """Open the TLS connection in a background thread so the first real lookup doesn't pay the handshake."""
        if not url:
            return

        def _warm():
            try:
                with session_lock:
                    self.session.get(url, timeout=10)
                logger.debug(f"{label} vault connection prewarmed")
            except Exception as e:
                logger.debug(f"{label} vault prewarm skipped (non-fatal): {e}")

        threading.Thread(target=_warm, daemon=True, name=f"{label.lower()}-vault-prewarm").start()
