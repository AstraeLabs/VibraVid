# 22.11.26

from __future__ import annotations

import base64
import logging
import secrets

import requests

from VibraVid.core.drm.cdm.remote.remote_cdm_api import InvalidSession, Key

logger = logging.getLogger("VibraVid.AnalChrome")


def _pssh_to_bytes(pssh_or_wrm) -> bytes:
    if isinstance(pssh_or_wrm, (bytes, bytearray)):
        return bytes(pssh_or_wrm)
    dumps = getattr(pssh_or_wrm, "dumps", None)
    if callable(dumps):
        return base64.b64decode(dumps())
    if isinstance(pssh_or_wrm, str):
        return base64.b64decode(pssh_or_wrm)
    raise TypeError(f"AnalChrome CDM: unsupported pssh type: {type(pssh_or_wrm)!r}")


class AnalChromeCdm:
    def __init__(self, host: str, secret: str = "", backend: str | None = None, scheme: str | None = None, **_ignored):
        host = str(host or "").strip().rstrip("/")
        if not host:
            raise ValueError("AnalChrome CDM: 'host' is required")

        self.host = host
        self.secret = str(secret or "").strip()
        self._backend = str(backend).strip().lower() if backend else None
        self._scheme = str(scheme).strip().lower() if scheme else None

        self._http = requests.Session()
        if self.secret:
            self._http.headers["X-API-Key"] = self.secret
        
        self._sessions: dict[bytes, str | None] = {}
        self._pending_cert: dict[bytes, tuple[bool, str | None]] = {}
        self._cached_keys: dict[bytes, list[Key]] = {}

    def open(self) -> bytes:
        session_id = secrets.token_bytes(16)
        self._sessions[session_id] = None
        self._pending_cert[session_id] = (False, None)
        self._cached_keys[session_id] = []
        return session_id

    def close(self, session_id: bytes) -> None:
        self._sessions.pop(session_id, None)
        self._pending_cert.pop(session_id, None)
        self._cached_keys.pop(session_id, None)

    def set_service_certificate(self, session_id: bytes, certificate) -> None:
        if session_id not in self._sessions:
            raise InvalidSession(f"AnalChrome CDM: invalid session ID: {session_id.hex()}")
        
        cert_b64 = None
        if certificate:
            if isinstance(certificate, str):
                cert_b64 = certificate.strip().replace("\n", "").replace(" ", "")
            elif isinstance(certificate, (bytes, bytearray)):
                cert_b64 = base64.b64encode(certificate).decode("utf-8")
            
        # Queued locally and attached to the next /challenge call.
        self._pending_cert[session_id] = (True, cert_b64)

    def get_license_challenge(self, session_id: bytes, pssh_or_wrm, *_args, **_kwargs) -> bytes:
        if session_id not in self._sessions:
            raise InvalidSession(f"AnalChrome CDM: invalid session ID: {session_id.hex()}")
        
        pssh_bytes = _pssh_to_bytes(pssh_or_wrm)
        payload: dict = {"pssh": base64.b64encode(pssh_bytes).decode("utf-8")}
        remote_sid = self._sessions[session_id]
        if remote_sid:
            payload["session_id"] = remote_sid
        elif self._backend or self._scheme:
            if self._backend:
                payload["system"] = self._backend
            if self._scheme:
                payload["scheme"] = self._scheme

        has_cert, cert_b64 = self._pending_cert.get(session_id, (False, None))
        if has_cert:
            payload["certificate"] = cert_b64

        r = self._http.post(f"{self.host}/challenge", json=payload, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"AnalChrome CDM challenge failed: {r.text}")
        
        data = r.json()
        self._sessions[session_id] = data["session_id"]
        self._pending_cert[session_id] = (False, None)
        return base64.b64decode(data["challenge"])

    def parse_license(self, session_id: bytes, license_message) -> None:
        if session_id not in self._sessions:
            raise InvalidSession(f"AnalChrome CDM: invalid session ID: {session_id.hex()}")

        remote_sid = self._sessions[session_id]
        if not remote_sid:
            raise InvalidSession("AnalChrome CDM: get_license_challenge must be called before parse_license")
        
        if isinstance(license_message, str):
            lic_b64 = license_message
        elif isinstance(license_message, (bytes, bytearray)):
            lic_b64 = base64.b64encode(license_message).decode("utf-8")
        else:
            lic_b64 = base64.b64encode(bytes(license_message)).decode("utf-8")

        parse_payload: dict = {"session_id": remote_sid, "license": lic_b64}
        if self._backend:
            parse_payload["system"] = self._backend
        if self._scheme:
            parse_payload["scheme"] = self._scheme
        r = self._http.post(f"{self.host}/parse", json=parse_payload, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"AnalChrome CDM parse failed: {r.text}")

        # Server auto-closes the native session once /parse completes.
        self._sessions[session_id] = None
        raw_keys = r.json().get("keys", [])
        self._cached_keys[session_id] = [Key(k["kid"], k["key"], "CONTENT") for k in raw_keys if k.get("key")]
        logger.info(f"AnalChrome CDM: parsed license and cached {len(self._cached_keys[session_id])} keys")

    def get_keys(self, session_id: bytes, type_: str | None = None) -> list[Key]:
        keys = self._cached_keys.get(session_id, [])
        return [k for k in keys if k.type == type_] if type_ else keys


def build_anal_chrome_cdm(cfg: dict | None = None) -> AnalChromeCdm:
    """Build an AnalChromeCdm client from a remote_cdm config mapping using host and secret."""
    cfg = dict(cfg or {})
    return AnalChromeCdm(
        host=cfg.get("host", ""),
        secret=cfg.get("secret", ""),
        backend=cfg.get("backend"),
        scheme=cfg.get("scheme"),
    )


def remote_system(cfg: dict) -> str:
    return "widevine"


def label(cfg: dict) -> str:
    """Short label shown in logs (``analchrome``)."""
    return str(cfg.get("id") or "analchrome")


def describe(cfg: dict) -> dict:
    """Name / device type / level shown for this CDM (it is a Chrome CDM, level unknown)."""
    return {"name": "analchrome", "type": "CHROME", "security_level": None}


def build(cfg: dict, system: str) -> AnalChromeCdm:
    """Build the client from a ``DRM.remote_cdm.analchrome`` block (Widevine only)."""
    if system != "widevine":
        raise ValueError("AnalChrome CDM is Widevine only")
    cfg = dict(cfg)
    cfg.pop("system", None)
    return build_anal_chrome_cdm(cfg)
