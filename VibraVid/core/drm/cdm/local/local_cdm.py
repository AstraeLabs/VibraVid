# 04.11.26

import importlib.util
import os
from pathlib import Path

PLAYREADY_IDENTITY_FILES = ("extracted.enc.plain", "extracted.sig.plain", "extracted.cert")
WIDEVINE_IDENTITY_FILES = ("metadata.yml", "client_id.bin")  # + private_key.der or private_key.pem


def _expand(value) -> str:
    return os.path.expandvars(os.path.expanduser(str(value))).strip() if value else ""


def _widevine_private_key_file(path: str) -> str | None:
    """``private_key.der`` or ``private_key.pem`` inside a Widevine identity folder, whichever exists."""
    for name in ("private_key.der", "private_key.pem"):
        candidate = os.path.join(path, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _missing_identity_files(system: str, path: str) -> list[str]:
    if system == "widevine":
        missing = [name for name in WIDEVINE_IDENTITY_FILES if not os.path.isfile(os.path.join(path, name))]
        if _widevine_private_key_file(path) is None:
            missing.append("private_key.der/.pem")
        return missing
    return [name for name in PLAYREADY_IDENTITY_FILES if not os.path.isfile(os.path.join(path, name))]


def validate_local_cdm(cfg: dict) -> str | None:
    """Why a ``DRM.local_cdm.<id>`` block cannot be used (``None`` when it can). Cheap: only looks at the files."""
    if not isinstance(cfg, dict) or not cfg:
        return "the block is empty"

    system = str(cfg.get("system", "playready")).lower()
    if system not in ("playready", "widevine"):
        return f"system '{system}' is not supported (local_cdm is Widevine or PlayReady only)"

    path, loader = _expand(cfg.get("path")), _expand(cfg.get("loader"))
    if loader:
        if not os.path.isfile(loader):
            return f"loader '{loader}' does not exist"
        if path and not os.path.exists(path):
            return f"path '{path}' does not exist"
        return None

    if not path:
        return "'path' is required (a .wvd/.prd file, or a folder with the raw identity files)"
    if os.path.isdir(path):
        missing = _missing_identity_files(system, path)
        return f"folder '{path}' lacks {', '.join(missing)}" if missing else None

    extension = ".wvd" if system == "widevine" else ".prd"
    if not path.lower().endswith(extension):
        return f"'{path}' is neither a {extension} file nor a folder"
    return None if os.path.isfile(path) else f"file '{path}' does not exist"


def _build_playready_device(path: str):
    from pyplayready.device import Device

    if os.path.isdir(path):
        def read(name: str) -> bytes:
            with open(os.path.join(path, name), "rb") as f:
                return f.read()

        return Device(
            group_key=None,
            encryption_key=read(PLAYREADY_IDENTITY_FILES[0]),
            signing_key=read(PLAYREADY_IDENTITY_FILES[1]),
            group_certificate=read(PLAYREADY_IDENTITY_FILES[2]),
        )

    return Device.load(path)


def _build_widevine_device(path: str):
    from pywidevine.device import Device, DeviceTypes

    if not os.path.isdir(path):
        return Device.load(path)

    import yaml

    with open(os.path.join(path, "metadata.yml"), "rb") as f:
        meta = yaml.safe_load(f) or {}
    wvd_meta = dict(meta.get("wvd", {}))
    device_type = str(wvd_meta.pop("device_type", "ANDROID")).upper()
    security_level = int(wvd_meta.pop("security_level", 3))

    with open(os.path.join(path, "client_id.bin"), "rb") as f:
        client_id = f.read()

    private_key_file = _widevine_private_key_file(path)
    if private_key_file is None:
        raise FileNotFoundError(f"'{path}' lacks private_key.der/.pem")
    with open(private_key_file, "rb") as f:
        private_key = f.read()

    return Device(
        type_=DeviceTypes[device_type],
        security_level=security_level,
        flags=wvd_meta or None,
        private_key=private_key,
        client_id=client_id,
    )


def build_local_device(cfg: dict):
    """Build the pywidevine/pyplayready ``Device`` of a ``DRM.local_cdm.<id>`` block, in memory."""
    problem = validate_local_cdm(cfg)
    if problem:
        raise ValueError(f"local CDM '{cfg.get('id', '?')}': {problem}")

    path, loader = _expand(cfg.get("path")), _expand(cfg.get("loader"))
    if loader:
        spec = importlib.util.spec_from_file_location(f"_vibravid_local_cdm_{Path(loader).stem}", loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        load = getattr(module, "load_device", None)
        if not callable(load):
            raise ValueError(f"local CDM '{cfg.get('id', '?')}': {loader} has no load_device() function")
        return load(path) if path else load()

    system = str(cfg.get("system", "playready")).lower()
    return _build_widevine_device(path) if system == "widevine" else _build_playready_device(path)


def describe_local_cdm(cfg: dict) -> dict:
    """Name and security level of a local CDM block, read from the device itself (``{"error": ...}`` when it does not load)."""
    try:
        device = build_local_device(cfg)
        name = device.get_name() if hasattr(device, "get_name") else getattr(getattr(device, "type", None), "name", None)
        return {"name": str(name or cfg.get("id") or ""), "type": None, "security_level": int(device.security_level)}
    except Exception as e:
        return {"name": str(cfg.get("id") or ""), "type": None, "security_level": None, "error": str(e)}
