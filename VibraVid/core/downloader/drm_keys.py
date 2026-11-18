# 05.10.26

import logging
from typing import Any

from VibraVid.core.drm.system import DRMType

logger = logging.getLogger(__name__)
_DRM_TYPE_NAMES = {DRMType.WIDEVINE: "widevine", DRMType.PLAYREADY: "playready", DRMType.FAIRPLAY: "fairplay"}


def effective_manual_key(key: Any) -> Any:
    """The manual key to use: the one a service passes explicitly, else the ``--key`` given on a ``-i`` run."""
    if key:
        return key

    from VibraVid.core.ui.tracker import context_tracker
    return (context_tracker.site_options or {}).get("key") or None


def manual_keys(key: Any) -> list[str]:
    """Normalize a manually supplied key (one ``"kid:key"`` string or an iterable of them) to a list."""
    return [key] if isinstance(key, str) else list(key)


def selected_video_height(streams) -> int:
    """Height of the tallest selected video stream, 0 when none is selected: what the per-quality ``cdm`` rules compare against."""
    tallest = 0
    for stream in streams or []:
        if getattr(stream, "type", "") != "video" or not getattr(stream, "selected", False):
            continue

        height = getattr(stream, "height", 0) or 0
        if not height:
            try:
                height = int((getattr(stream, "resolution", "") or "").lower().replace("p", "").split("x")[-1])
            except ValueError:
                height = 0
        tallest = max(tallest, int(height))

    return tallest


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
    height: int | None = None,
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
    if height is not None:
        drm_manager.select_cdm(height)
    
    effective_pref = _effective_preference(drm_psshs, preference, cross_drm_fallback)

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

def _effective_preference(drm_psshs: dict[str, list[dict]], preference: str, cross_drm_fallback: bool) -> str:
    """*preference*, or the other of Widevine/PlayReady when the manifest only carries that one."""
    if cross_drm_fallback and not drm_psshs.get(preference):
        other = DRMType.PLAYREADY if preference == DRMType.WIDEVINE else DRMType.WIDEVINE
        if drm_psshs.get(other):
            return other
    return preference


def build_key_fallback(
    drm_manager,
    drm_psshs: dict[str, list[dict]],
    *,
    preference: str,
    license_url: str | None,
    license_headers: dict | None,
    license_data: dict | None = None,
    license_certificate: str | None = None,
    license_request_fn=None,
    cross_drm_fallback: bool = False,
):
    """KeyFallback for the same DRM type/license fetch_drm_keys resolved with, or None when there is nothing to fall back on."""
    from VibraVid.core.drm.key_fallback import KeyFallback

    effective_pref = _effective_preference(drm_psshs, preference, cross_drm_fallback)
    pssh_list = drm_psshs.get(effective_pref) or []
    if drm_manager is None or not pssh_list or effective_pref not in _DRM_TYPE_NAMES:
        return None

    return KeyFallback(
        drm_manager,
        _DRM_TYPE_NAMES[effective_pref],
        pssh_list,
        license_url=license_url,
        license_headers=license_headers,
        license_data=license_data,
        license_certificate=license_certificate,
        license_request_fn=license_request_fn,
    )
