# 05.10.26

import logging
from typing import Any

from VibraVid.core.drm.system import DRMType

logger = logging.getLogger(__name__)


def manual_keys(key: Any) -> list[str]:
    """Normalize a manually supplied key (one ``"kid:key"`` string or an iterable of them) to a list."""
    return [key] if isinstance(key, str) else list(key)


def fetch_drm_keys(
    drm_manager,
    drm_psshs: dict[str, list[dict]],
    *,
    preference: str,
    license_url: str | None,
    license_headers: dict | None,
    key: Any,
    license_data: dict | None = None,
    license_certificate: str | None = None,
    license_request_fn=None,
    cross_drm_fallback: bool = False,
    fairplay: bool = False,
    swallow_errors: bool = False,
    widevine_license_data: bool = True,
    manual_key_fallback: bool = True,
) -> list[str]:
    """
    ===================================  =====  =====  =====  =================
    flag                                 HLS    DASH   ISM    DASH extra audio
    ===================================  =====  =====  =====  =================
    ``cross_drm_fallback``               no     yes    yes    yes
    ``fairplay``                         yes    no     no     no
    ``swallow_errors``                   yes    no     yes    no
    ``widevine_license_data``            yes    yes    yes    no
    ``manual_key_fallback``              yes    yes    yes    no
    ``license_request_fn`` forwarded     yes    yes    no     yes
    ===================================  =====  =====  =====  =================
    """
    keys = None
    effective_pref = preference
    if cross_drm_fallback and not drm_psshs.get(effective_pref):
        other = DRMType.PLAYREADY if effective_pref == DRMType.WIDEVINE else DRMType.WIDEVINE
        if drm_psshs.get(other):
            effective_pref = other

    def _resolve(label: str, call):
        if not swallow_errors:
            return call()
        try:
            return call()
        except Exception as exc:
            logger.error(f"{label} key fetch failed: {exc}")
            return None

    if effective_pref == DRMType.WIDEVINE and drm_psshs.get(DRMType.WIDEVINE):
        keys = _resolve(
            "Widevine",
            lambda: drm_manager.get_wv_keys(
                drm_psshs[DRMType.WIDEVINE],
                license_url,
                license_data=license_data if widevine_license_data else None,
                license_certificate=license_certificate,
                headers=license_headers,
                key=key,
                license_request_fn=license_request_fn,
            ),
        )

    if effective_pref == DRMType.PLAYREADY and drm_psshs.get(DRMType.PLAYREADY):
        keys = _resolve(
            "PlayReady",
            lambda: drm_manager.get_pr_keys(
                drm_psshs[DRMType.PLAYREADY],
                license_url,
                headers=license_headers,
                key=key,
                license_data=license_data,
                license_request_fn=license_request_fn,
            ),
        )

    if fairplay and effective_pref == DRMType.FAIRPLAY and drm_psshs.get(DRMType.FAIRPLAY):
        keys = _resolve("FairPlay", lambda: drm_manager.get_fp_keys(drm_psshs[DRMType.FAIRPLAY], license_url, key=key))

    if manual_key_fallback and not keys and key:
        keys = manual_keys(key)

    return keys or []
