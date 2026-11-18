# 22.02.25

import logging

from VibraVid.core.decryptor._models import detect_encryption_info, detect_media_info
from VibraVid.utils.os import os_manager

logger = logging.getLogger(__name__)
PROBE_BYTES = 1 * 1024 * 1024  # 1 MB — safety-net ceiling for in-flight accumulation
PROBE_BYTES_FAST = 100 * 1024  # 100 KB — cheap preflight Range probe tried before the real download starts


class DRMProbe:
    def probe(self, url: str, headers: dict, client, size: int = PROBE_BYTES) -> tuple:
        """Returns ``(encrypted: bool, scheme: str | None, is_widevine: bool, kid: str | None, pssh_b64: str | None)``."""
        try:
            raw = self._fetch_bytes(url, headers, client, size=size)
            if not raw:
                return False, None, False, None, None

            info = self._parse_bytes(raw)
            if not info.encrypted:
                logger.debug(f"DRMProbe: no encryption markers found in first {len(raw)} bytes.")
                return False, None, False, None, None

            self._report(info.scheme, info.kid, info.is_widevine, info)
            return True, info.scheme, info.is_widevine, info.kid, info.pssh_b64

        except Exception as exc:
            logger.debug(f"DRMProbe failed (non-fatal): {exc}")
            return False, None, False, None, None

    def fetch(self, url: str, headers: dict, client, size: int = PROBE_BYTES) -> bytes | None:
        """Public wrapper for ``_fetch_bytes`` — for callers that need the raw bytes themselves rather than just the DRM verdict."""
        return self._fetch_bytes(url, headers, client, size=size)

    def inspect(self, raw: bytes) -> tuple:
        """Inspect already-downloaded bytes (in-flight probe, no second request).
        Returns ``(encrypted, scheme, is_widevine, kid, pssh_b64)``."""
        try:
            if not raw:
                return False, None, False, None, None

            info = self._parse_bytes(raw)
            if not info.encrypted:
                logger.debug(f"DRMProbe: no encryption markers found in first {len(raw)} bytes.")
                return False, None, False, None, None

            self._report(info.scheme, info.kid, info.is_widevine, info)
            return True, info.scheme, info.is_widevine, info.kid, info.pssh_b64

        except Exception as exc:
            logger.debug(f"DRMProbe.inspect failed (non-fatal): {exc}")
            return False, None, False, None, None

    def inspect_full(self, raw: bytes) -> tuple:
        """Like ``inspect()``, but also returns label-ready media metadata."""
        try:
            if not raw:
                return False, None, False, None, None, {}

            with os_manager.temp_binary_file(raw, suffix=".mp4probe") as tmp_path:
                info, media = detect_media_info(tmp_path)

            if not info.encrypted:
                logger.debug(f"DRMProbe: no encryption markers found in first {len(raw)} bytes.")
            else:
                self._report(info.scheme, info.kid, info.is_widevine, info)

            return info.encrypted, info.scheme, info.is_widevine, info.kid, info.pssh_b64, media

        except Exception as exc:
            logger.debug(f"DRMProbe.inspect_full failed (non-fatal): {exc}")
            return False, None, False, None, None, {}

    def _fetch_bytes(self, url: str, headers: dict, client, size: int = PROBE_BYTES) -> bytes | None:
        """Fetch the first *size* bytes of the URL using a Range request, returning the raw bytes (or None on failure)."""
        probe_headers = {**headers, "Range": f"bytes=0-{size - 1}"}
        resp = client.get(url, headers=probe_headers, timeout=15)

        if resp.status_code not in (200, 206):
            logger.debug(f"DRMProbe: unexpected status {resp.status_code} — skipping.")
            return None

        raw = resp.content[:size]
        return raw if raw else None

    @staticmethod
    def _parse_bytes(raw: bytes):
        """Write *raw* to a temp file, run ``detect_encryption_info``, then delete."""
        with os_manager.temp_binary_file(raw, suffix=".mp4probe") as tmp_path:
            return detect_encryption_info(tmp_path)

    @staticmethod
    def _report(scheme: str | None, kid: str | None, is_widevine: bool, info=None) -> None:
        """Log a summary of the detected encryption info."""
        label = "Widevine" if is_widevine else (scheme or "unknown DRM")
        logger.info(f"DRMProbe: encryption detected — scheme={scheme or 'unknown'}, kid={kid or 'n/a'}, DRM=[{label}]")
        if info is not None and getattr(info, "has_fragment_signal", False):
            if info.fully_encrypted:
                logger.info(f"DRMProbe: fully-encrypted chunk — {info.encrypted_fragments} encrypted fragment(s), 0 clear (senc-driven, scheme-agnostic).")
            elif info.clear_fragments:
                logger.info(f"DRMProbe: clear-lead suspected — {info.clear_fragments} clear fragment(s) vs {info.encrypted_fragments} encrypted; key-sanity must run on a senc-carrying fragment.")
