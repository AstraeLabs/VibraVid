# 18.07.25

import os
import sys
import threading

from .binary_paths import binary_paths
from .checker import (
    check_deno,
    check_dovi_tool,
    check_ffmpeg,
    check_flux,
    check_mkvmerge,
    check_mkvpropedit,
    check_velora,
    check_yt_dlp,
)
from .device_install import check_device_prd_path, check_device_wvd_path

is_binary_installation = getattr(sys, "frozen", False)
_ffmpeg_path = None
_ffprobe_path = None
_wvd_path = None
_prd_path = None
_velora_path = None
_dovi_tool_path = None
_mkvmerge_path = None
_mkvpropedit_path = None
_flux_path = None
_initialized = False
_init_lock = threading.Lock()


def _is_alive(path) -> bool:
    """Return True if a resolved binary path still points at an existing file."""
    return bool(path) and os.path.isfile(path)


def _drop_cached(*names: str) -> None:
    """Forget any previously resolved location for the given binaries so the next check_* call re-scans disk"""
    ext = ".exe" if binary_paths.system == "windows" else ""
    for name in names:
        binary_paths.invalidate_binary(f"{name}{ext}")


def _initialize_paths():
    """
    Resolve and cache all binary paths exactly once.

    Uses double-checked locking so that:
    - After the first initialization, every getter returns instantly with zero locking overhead (outer ``if _initialized`` check).
    - During the first initialization, only one thread runs the checks
    """
    global _ffmpeg_path, _ffprobe_path
    global _wvd_path, _prd_path, _velora_path
    global _dovi_tool_path, _mkvmerge_path, _mkvpropedit_path, _flux_path
    global _initialized

    # Fast path: already initialized, return immediately.
    if _initialized:
        return

    with _init_lock:
        # A concurrent thread may have finished initialization while we were waiting for the lock.
        if _initialized:
            return

        _ffmpeg_path, _ffprobe_path = check_ffmpeg()
        _wvd_path = check_device_wvd_path()
        _prd_path = check_device_prd_path()
        _velora_path = check_velora()
        _dovi_tool_path = check_dovi_tool()
        _mkvmerge_path = check_mkvmerge()
        _mkvpropedit_path = check_mkvpropedit()
        _flux_path = check_flux()
        _initialized = True


def get_is_binary_installation() -> bool:
    return is_binary_installation


def get_ffmpeg_path() -> str:
    global _ffmpeg_path, _ffprobe_path
    if not _initialized:
        _initialize_paths()
    if not _is_alive(_ffmpeg_path) or not _is_alive(_ffprobe_path):
        _drop_cached("ffmpeg", "ffprobe")
        _ffmpeg_path, _ffprobe_path = check_ffmpeg()
    return _ffmpeg_path


def get_ffprobe_path() -> str:
    global _ffmpeg_path, _ffprobe_path
    if not _initialized:
        _initialize_paths()
    if not _is_alive(_ffmpeg_path) or not _is_alive(_ffprobe_path):
        _drop_cached("ffmpeg", "ffprobe")
        _ffmpeg_path, _ffprobe_path = check_ffmpeg()
    return _ffprobe_path


def get_wvd_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _wvd_path


def get_prd_path() -> str:
    if not _initialized:
        _initialize_paths()
    return _prd_path


def get_velora_path() -> str:
    global _velora_path
    if not _initialized:
        _initialize_paths()
    if not _is_alive(_velora_path):
        _drop_cached("velora")
        _velora_path = check_velora()
    return _velora_path

def get_yt_dlp_path() -> str | None:
    """Return path to yt-dlp binary, downloading if needed."""
    return check_yt_dlp()


def get_deno_path() -> str | None:
    """Return path to deno binary, downloading if needed."""
    return check_deno()

def get_flux_path() -> str | None:
    """Return the resolved `flux` binary path, or None if it isn't available (optional tool)."""
    global _flux_path
    if not _initialized:
        _initialize_paths()
    if not _is_alive(_flux_path):
        _drop_cached("flux")
        _flux_path = check_flux()
    return _flux_path


def get_dovi_tool_path() -> str:
    global _dovi_tool_path
    if not _initialized:
        _initialize_paths()
    if not _is_alive(_dovi_tool_path):
        _drop_cached("dovi_tool")
        _dovi_tool_path = check_dovi_tool()
    return _dovi_tool_path


def get_mkvmerge_path() -> str:
    global _mkvmerge_path
    if not _initialized:
        _initialize_paths()
    if not _is_alive(_mkvmerge_path):
        _drop_cached("mkvmerge")
        _mkvmerge_path = check_mkvmerge()
    return _mkvmerge_path


def get_mkvpropedit_path() -> str | None:
    global _mkvpropedit_path
    if not _initialized:
        _initialize_paths()

    if not _is_alive(_mkvpropedit_path):
        _drop_cached("mkvpropedit")
        _mkvpropedit_path = check_mkvpropedit()
    
    return _mkvpropedit_path


def read_device_wvd(cdm_device_path) -> dict | None:
    """Structured info of a Widevine .wvd: path, DeviceTypes name (CHROME / ANDROID), security level, model, kind (TV / Emulator / Phone)."""
    if cdm_device_path is None:
        return None

    from pywidevine.device import Device
    device = Device.load(cdm_device_path)

    info = {ci.name: ci.value for ci in device.client_id.client_info}
    model = info.get("model_name", "N/A")
    device_name = info.get("device_name", "").lower()
    build_info = info.get("build_info", "").lower()

    is_emulator = any(x in device_name for x in ["generic", "sdk", "emulator", "x86"]) or any(
        x in build_info for x in ["test-keys", "userdebug"]
    )

    if "tv" in model.lower():
        kind = "TV"
    elif is_emulator:
        kind = "Emulator"
    else:
        kind = "Phone"

    return {
        "system": "widevine",
        "path": cdm_device_path,
        "type": device.type.name,
        "security_level": device.security_level,
        "system_id": device.system_id,
        "model": model,
        "kind": kind,
    }


def read_device_prd(cdm_device_path) -> dict | None:
    """Structured info of a PlayReady .prd: path, certificate type, security level, manufacturer / model."""
    if cdm_device_path is None:
        return None

    from pyplayready.device import Device
    from pyplayready.system.bcert import BCertCertType, BCertObjType

    device = Device.load(cdm_device_path)
    cert_chain = device.group_certificate
    leaf_cert = cert_chain.get(0)

    basic = leaf_cert.get_attribute(BCertObjType.BASIC)
    cert_type = BCertCertType(basic.attribute.cert_type).name if basic else "N/A"
    security_level = basic.attribute.security_level if basic else device.security_level

    def un_pad(b: bytes) -> str:
        return b.rstrip(b"\x00").decode("utf-8", errors="ignore")

    manufacturer = model = model_number = "N/A"
    mfr = leaf_cert.get_attribute(BCertObjType.MANUFACTURER)
    if mfr:
        manufacturer = un_pad(mfr.attribute.manufacturer_name)
        model = un_pad(mfr.attribute.model_name)
        model_number = un_pad(mfr.attribute.model_number)

    return {
        "system": "playready",
        "path": cdm_device_path,
        "type": cert_type,
        "security_level": security_level,
        "manufacturer": manufacturer,
        "model": model,
        "model_number": model_number,
    }


def get_info_wvd(cdm_device_path):
    info = read_device_wvd(cdm_device_path)
    if info is None:
        return None

    return (
        f"[red]Load [cyan]{info['kind']} [red]{info['path']}[cyan] | "
        f"[cyan]Security: [red]L{info['security_level']} [cyan]| "
        f"[cyan]Model: [red]{info['model']} [cyan]| "
        f"[cyan]SysID: [red]{info['system_id']}"
    )


def get_info_prd(cdm_device_path):
    info = read_device_prd(cdm_device_path)
    if info is None:
        return None

    return (
        f"[red]Load [cyan]{info['type']} [red]{info['path']}[cyan] | "
        f"[cyan]Security: [red]SL{info['security_level']} [cyan]| "
        f"[cyan]Model: [red]{info['manufacturer']} {info['model']} {info['model_number']} [cyan]"
    )


def resolve_service_cdm_info(site_name: str | None, height: int | None = None) -> dict:
    """Resolve the best available CDM for the given site and height, returning structured info for both Widevine and PlayReady."""
    from .device_install import describe_remote_cdm, resolve_cdm

    chosen = resolve_cdm(site_name, height)

    def describe(system: str, path: str | None, remote: dict | None, reader) -> dict | None:
        if remote:
            return {"system": system, "path": None, "remote": True, **describe_remote_cdm(remote)}
        if not path:
            return None
        try:
            return {**reader(path), "remote": False}
        except Exception as e:
            return {"path": path, "remote": False, "error": str(e)}

    return {
        "widevine": describe("widevine", chosen.wvd_path, chosen.widevine_remote, read_device_wvd),
        "playready": describe("playready", chosen.prd_path, chosen.playready_remote, read_device_prd),
    }
