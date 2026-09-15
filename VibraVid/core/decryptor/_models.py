# 01.04.26

import json
import logging
import os
import subprocess
from collections import OrderedDict
from dataclasses import dataclass

from VibraVid.setup import get_flux_path

from ..drm.system import _DRMSystems

logger = logging.getLogger(__name__)

_KNOWN_SCHEMES = {"cenc", "cens", "cbcs", "cbc1"}


@dataclass
class EncryptionInfo:
    encrypted: bool = False
    scheme: str | None = None
    kid: str | None = None
    pssh_b64: str | None = None
    is_widevine: bool = False


def _run_flux_dump(file_path: str) -> dict | None:
    """Runs `flux -d -j <file_path>` and returns the parsed DumpReport dict, or None."""
    flux_path = get_flux_path()
    if not flux_path:
        return None

    try:
        logger.info(f"Running flux cmd: {flux_path} -d -j {file_path}")
        result = subprocess.run(
            [flux_path, "-d", "-j", file_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug(f"flux -d -j failed to run for {file_path}: {exc}")
        return None
    if result.returncode != 0:
        logger.debug(f"flux -d -j exited {result.returncode} for {file_path}: {result.stderr.strip()}")
        return None
    try:
        return json.loads(result.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        logger.debug(f"flux -d -j produced no JSON for {file_path}: {exc}")
        return None


def _parse_flux_json(report: dict) -> EncryptionInfo:
    """Turns a `flux -d -j` DumpReport into EncryptionInfo."""
    info = EncryptionInfo()

    pssh_systems = [s for s in (report.get("pssh_systems") or []) if s]
    info.is_widevine = "widevine" in pssh_systems

    streams = report.get("streams") or []
    for stream in streams:
        if stream.get("is_encrypted"):
            info.encrypted = True
            if info.kid is None:
                kid = (stream.get("crypto") or {}).get("default_kid")
                if kid:
                    info.kid = kid

    first_scheme: str | None = None
    for stream in streams:
        scheme = (stream.get("crypto") or {}).get("scheme")
        if not scheme:
            continue
        scheme = scheme.lower()
        if first_scheme is None:
            first_scheme = scheme
        if info.scheme is None and scheme in _KNOWN_SCHEMES:
            info.scheme = scheme

    if info.scheme is None:
        info.scheme = first_scheme

    if pssh_systems:
        info.encrypted = True

    if info.encrypted:
        info.pssh_b64 = _select_preferred_pssh(info.is_widevine, info.kid)

    return info


def _select_preferred_pssh(is_widevine: bool, kid: str | None) -> str | None:
    """Return a real base64 Widevine PSSH box synthesized from the KID."""
    if not is_widevine or not kid:
        return None

    try:
        return _DRMSystems.build_widevine_pssh_from_kid(kid)
    except Exception as exc:
        logger.debug(f"Widevine PSSH synthesis failed for KID {kid}: {exc}")
        return None


# Session cache: avoid re-running `flux -d -j` on the same file within a download.
_DETECT_CACHE_MAX = 512
_detect_cache: "OrderedDict[tuple[str, int, int], EncryptionInfo]" = OrderedDict()

def _detect_cache_key(file_path: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(file_path)
        return (os.path.abspath(file_path), st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def detect_encryption_info(file_path: str) -> EncryptionInfo:
    """Detect encryption metadata via `flux -d -j`."""
    cache_key = _detect_cache_key(file_path)
    if cache_key is not None and cache_key in _detect_cache:
        _detect_cache.move_to_end(cache_key)
        return _detect_cache[cache_key]

    report = _run_flux_dump(file_path)
    if report is None or not report.get("streams"):
        info = EncryptionInfo()
    else:
        info = _parse_flux_json(report)

    if cache_key is not None:
        _detect_cache[cache_key] = info
        _detect_cache.move_to_end(cache_key)
        if len(_detect_cache) > _DETECT_CACHE_MAX:
            _detect_cache.popitem(last=False)
    return info
