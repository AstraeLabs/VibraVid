# 24.10.26

import logging

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)


def _switch(site: str | None, name: str, section: str, key: str) -> bool:
    """login.toml ``process.<name>`` of *site* when set, otherwise the global config.toml key. Off when neither says otherwise."""
    if site:
        from VibraVid.services._base.site_extra_args import resolve_site_process

        value = resolve_site_process(site).get(name)
        if value is not None:
            return value
    return bool(config_manager.config.get_bool(section, key, default=False))


def any_selected_encrypted(streams) -> bool:
    """True if any selected stream carries DRM or an encryption method (its data is ciphertext until decrypted)."""
    drms = (getattr(s, "drm", None) for s in streams or [] if getattr(s, "selected", False))
    return any(d is not None and (d.method is not None or d.is_encrypted()) for d in drms)


def live_decrypt_allowed(site: str | None = None) -> bool:
    """True only when in-flight decryption is switched on: ``process.live_decrypt`` of the site in login.toml, else ``DOWNLOAD.use_live_decrypt`` (off if neither is set); ``--no-live-decrypt`` always turns it off. Otherwise the segments are decrypted after the download."""
    site = site or context_tracker.site_name
    if context_tracker.no_live_decrypt:
        return False
    return _switch(site, "live_decrypt", "DOWNLOAD", "use_live_decrypt")


def live_mux_allowed(site: str | None = None, encrypted_tracks: bool = False) -> tuple[bool, str]:
    """Whether the streaming-mux fast path may run for *site*, with the reason when it may not."""
    site = site or context_tracker.site_name
    if context_tracker.no_livemux:
        return False, "--no-livemux"
    if encrypted_tracks and not live_decrypt_allowed(site):
        return False, "live decrypt is off and some selected tracks are encrypted"
    if context_tracker.force_livemux:
        return True, ""
    if not _switch(site, "live_mux", "PROCESS", "use_live_mux"):
        return False, f"process.live_mux is off for {site!r} (login.toml) or PROCESS.use_live_mux is false"
    return True, ""
