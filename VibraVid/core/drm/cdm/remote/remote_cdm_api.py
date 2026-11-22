# 09.10.26

import importlib
import importlib.util
import re
from uuid import UUID

DEFAULT_AUTH_HEADER = "x-api-key"
_KIND_ALIASES = {"custom_api_1": "decryptlabs", "anal_chrome": "analchrome"}
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_RESERVED_KINDS = {"remote_cdm_api"}


def _clean_kid(kid) -> str:
    return str(kid).replace("-", "").lower()


def _to_uuid(kid: str) -> UUID:
    clean = kid.replace("-", "")
    return UUID(hex=clean if len(clean) == 32 else clean.ljust(32, "0"))


class Key:
    """Key object exposing the attributes read by the Widevine (`kid`) and PlayReady (`key_id`) code paths."""
    def __init__(self, kid, key, type_: str = "CONTENT"):
        self.kid = _to_uuid(kid) if isinstance(kid, str) else kid
        self.key = bytes.fromhex(key) if isinstance(key, str) else key
        self.type = type_

    @property
    def key_id(self) -> UUID:
        return self.kid


class InvalidSession(Exception):
    """Raised when the session ID is unknown."""


def __getattr__(name: str):
    """Lazy re-export of the DecryptLabs client (RemoteCdmApi is its old name) without a circular import."""
    if name in ("RemoteCdmApi", "DecryptLabsCdm"):
        from VibraVid.core.drm.cdm.remote import decryptlabs

        return getattr(decryptlabs, name)
    raise AttributeError(name)


def load_remote_client(cfg: dict):
    """Client module of a `DRM.remote_cdm.<id>` block, or None for a stock pywidevine / pyplayready remote CDM."""
    kind = str(cfg.get("type") or cfg.get("id") or "").strip().lower()
    kind = _KIND_ALIASES.get(kind, kind)
    if not _KIND_RE.match(kind) or kind in _RESERVED_KINDS:
        return None

    name = f"{__package__}.{kind}"
    if importlib.util.find_spec(name) is None:
        return None
    return importlib.import_module(name)


def remote_label(cfg: dict) -> str:
    """Short name of a remote CDM block for logs: the client's own label, otherwise its id (or ``device_name``)."""
    client = load_remote_client(cfg)
    if client is not None and hasattr(client, "label"):
        return client.label(cfg)
    return str(cfg.get("id") or cfg.get("device_name") or "?")


def build_remote_cdm(cfg: dict, system: str):
    """
    Build the remote CDM described by a `DRM.remote_cdm.<id>` config dict.
    """
    cfg = dict(cfg)
    client = load_remote_client(cfg)
    cfg.pop("type", None)
    cfg.pop("id", None)

    if client is not None:
        return client.build(cfg, system)

    if system == "widevine":
        from pywidevine.device import DeviceTypes
        from pywidevine.remotecdm import RemoteCdm

        device_types = {"ANDROID": DeviceTypes.ANDROID, "CHROME": DeviceTypes.CHROME}
        device_type = device_types.get(str(cfg.get("device_type", "")).upper())
        if device_type is None:
            raise ValueError(f"Unsupported remote CDM device type: {cfg.get('device_type')}")
        cfg["device_type"] = device_type
        return RemoteCdm(**cfg)

    from pyplayready.remote.remotecdm import RemoteCdm

    return RemoteCdm(**cfg)
