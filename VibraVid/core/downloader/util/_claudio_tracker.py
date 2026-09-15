# 22.02.25

import logging
import threading

from VibraVid.utils.vault.vault_1 import claudio_vault

logger = logging.getLogger(__name__)


class ClaudioTracker:
    def fire(self, title: str, media_type: str, site: str) -> None:
        """Fire-and-forget background thread to track a download in ClaudioVault."""
        try:
            threading.Thread(target=self._run, args=(title, media_type, site), daemon=False).start()
        except Exception:
            logger.debug("ClaudioTracker: failed to start background thread (ignored)")

    @staticmethod
    def _run(title: str, media_type: str, site: str) -> None:
        """Track a download in ClaudioVault.  Logs errors but never raises."""
        try:
            if not claudio_vault.is_connected:
                logger.warning("ClaudioTracker: claudio_vault not configured")
                return

            title_str = (title or "").strip() or "://generic"
            media_type_str = (media_type or "").strip()
            site_str = (site or "").strip().lower()
            _ = claudio_vault.track_download(title_str, media_type_str, site_str)
        except Exception as exc:
            logger.error(f"ClaudioTracker error: {exc}", exc_info=True)
