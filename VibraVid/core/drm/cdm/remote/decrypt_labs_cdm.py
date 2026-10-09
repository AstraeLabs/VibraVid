# 09.10.26

import base64
import secrets
from typing import Any
from uuid import UUID

import requests

DEFAULT_HOST = "https://keyxtractor.decryptlabs.com"


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


class DecryptLabsRemoteCDM:
    def __init__(
        self,
        secret: str,
        host: str = DEFAULT_HOST,
        device_name: str = "ChromeCDM",
        device_type: str | None = None,
        service_name: str | None = None,
        **_ignored,
    ):
        """
        Args:
            secret: Decrypt Labs API key.
            host: API host.
            device_name: Widevine: ChromeCDM, L1, L2 or L3. PlayReady: SL2 or SL3.
            device_type: Optional, "PLAYREADY" forces PlayReady mode.
            service_name: Optional service tag sent to the API for key caching.
        """
        self.secret = secret
        self.host = host.rstrip("/")
        self.device_name = device_name
        self.service_name = service_name or ""
        self._is_playready = (device_type or "").upper() == "PLAYREADY" or device_name.upper().startswith("SL")

        self._sessions: dict[bytes, dict[str, Any]] = {}
        self._pssh_b64: str | None = None
        self._required_kids: list[str] | None = None
        self._http = requests.Session()
        self._http.headers.update(
            {
                "decrypt-labs-api-key": self.secret,
                "Content-Type": "application/json",
                "User-Agent": "vibravid-decrypt-labs-cdm",
            }
        )

    @property
    def is_playready(self) -> bool:
        return self._is_playready

    def _session(self, session_id: bytes) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise InvalidSession(f"Invalid session ID: {session_id.hex()}")
        return self._sessions[session_id]

    def open(self) -> bytes:
        session_id = secrets.token_bytes(16)
        self._sessions[session_id] = {
            "service_certificate": None,
            "keys": [],
            "pssh": None,
            "challenge": None,
            "api_session_id": None,
            "tried_cache": False,
            "pssh_b64": None,
        }
        return session_id

    def close(self, session_id: bytes) -> None:
        self._session(session_id)
        del self._sessions[session_id]

    def set_pssh_b64(self, pssh_b64: str, session_id: bytes | None = None) -> None:
        if session_id is not None and session_id in self._sessions:
            self._sessions[session_id]["pssh_b64"] = pssh_b64
        self._pssh_b64 = pssh_b64

    def set_required_kids(self, kids: list, session_id: bytes | None = None) -> None:
        required = [_clean_kid(k) for k in kids]
        if session_id is not None and session_id in self._sessions:
            self._sessions[session_id]["required_kids"] = required
        self._required_kids = required

    def set_service_certificate(self, session_id: bytes, certificate: bytes | str | None) -> str:
        from pywidevine.cdm import Cdm as WidevineCdm

        session = self._session(session_id)
        if certificate is None:
            if not self._is_playready and self.device_name in ("L1", "L2"):
                session["service_certificate"] = base64.b64decode(WidevineCdm.common_privacy_cert)
                return f"Using default Widevine common privacy certificate for {self.device_name}"
            session["service_certificate"] = None
            return "No certificate set (not required for this device type)"

        session["service_certificate"] = base64.b64decode(certificate) if isinstance(certificate, str) else certificate
        return "Successfully set Service Certificate"

    def _post(self, path: str, payload: dict) -> dict:
        response = self._http.post(f"{self.host}{path}", json=payload, timeout=30)
        if response.status_code != 200:
            raise requests.RequestException(f"Decrypt Labs {path} failed: {response.status_code} {response.text}")

        data = response.json()
        if data.get("message") != "success":
            msg = data.get("message", "Unknown error")
            for field in ("details", "Error"):
                if field in data:
                    msg += f" - {field}: {data[field]}"
            if "service_certificate is required" in str(data):
                msg += " (no service certificate was set on the CDM session)"
            raise requests.RequestException(f"Decrypt Labs API error: {msg}")
        return data

    @staticmethod
    def _init_data(pssh, pssh_b64: str | None) -> str:
        if pssh_b64:
            return pssh_b64
        if hasattr(pssh, "dumps"):
            dumped = pssh.dumps()
            if isinstance(dumped, str):
                try:
                    base64.b64decode(dumped)
                    return dumped
                except ValueError:
                    return base64.b64encode(dumped.encode("utf-8")).decode("utf-8")
            return base64.b64encode(dumped).decode("utf-8")
        raise ValueError(f"Unsupported PSSH type {type(pssh)}: for PlayReady call set_pssh_b64() first")

    def _request_payload(self, session: dict, init_data: str, cached: bool) -> dict:
        payload = {"scheme": self.device_name, "init_data": init_data, "get_cached_keys_if_exists": cached}
        if self.service_name:
            payload["service"] = self.service_name
        if session["service_certificate"]:
            payload["service_certificate"] = base64.b64encode(session["service_certificate"]).decode("utf-8")
        return payload

    def get_license_challenge(
        self, session_id: bytes, pssh_or_wrm: Any, license_type: str = "STREAMING", privacy_mode: bool = True
    ) -> bytes:
        """Return the license challenge, or b"" when the API already returned every required key."""
        session = self._session(session_id)
        session["pssh"] = pssh_or_wrm
        init_data = self._init_data(pssh_or_wrm, session.get("pssh_b64") or self._pssh_b64)
        required = session.get("required_kids") or self._required_kids

        # L1/L2 always ask for cached keys first (the API optimises for them); others only once per session.
        ask_cache = self.device_name in ("L1", "L2") or not session["tried_cache"]
        data = self._post("/get-request", self._request_payload(session, init_data, ask_cache))

        if data.get("message_type") == "cached-keys" or "cached_keys" in data:
            cached_keys = self.parse_cached_keys(data.get("cached_keys", []))
            session["tried_cache"] = True

            have = {_clean_kid(k["kid"]) for k in cached_keys}
            missing = not cached_keys or (bool(required) and not set(required) <= have)
            if missing:
                try:
                    data = self._post("/get-request", self._request_payload(session, init_data, False))
                except requests.RequestException:
                    data = {}
                
                if "challenge" in data:
                    session["cached_keys"] = cached_keys
                    return self._store_challenge(session, data)

            session["keys"] = cached_keys
            return b""

        if "challenge" in data:
            return self._store_challenge(session, data)

        if session["tried_cache"]:
            return b""
        raise requests.RequestException(f"Unexpected Decrypt Labs response, fields: {list(data.keys())}")

    @staticmethod
    def _store_challenge(session: dict, data: dict) -> bytes:
        challenge = base64.b64decode(data["challenge"])
        session["challenge"] = challenge
        session["api_session_id"] = data["session_id"]
        return challenge

    def parse_license(self, session_id: bytes, license_message: bytes | str) -> None:
        session = self._session(session_id)
        if session["keys"] and "cached_keys" not in session:
            return  # final keys were already served from the cache
        if not session.get("challenge") or not session.get("api_session_id"):
            raise ValueError("No challenge available - call get_license_challenge first")

        if isinstance(license_message, str):
            if self._is_playready and license_message.lstrip().startswith("<"):
                license_message = license_message.encode("utf-8")
            else:
                try:
                    license_message = base64.b64decode(license_message)
                except ValueError:
                    license_message = license_message.encode("utf-8")

        data = self._post(
            "/decrypt-response",
            {
                "scheme": self.device_name,
                "session_id": session["api_session_id"],
                "init_data": self._init_data(session["pssh"], session.get("pssh_b64") or self._pssh_b64),
                "license_request": base64.b64encode(session["challenge"]).decode("utf-8"),
                "license_response": base64.b64encode(license_message).decode("utf-8"),
            },
        )

        keys = list(session.pop("cached_keys", []))
        known = {_clean_kid(k["kid"]) for k in keys}
        for license_key in self.parse_keys_response(data):
            if license_key["kid"] and _clean_kid(license_key["kid"]) not in known:
                keys.append(license_key)
        session["keys"] = keys

    def get_keys(self, session_id: bytes, type_: str | None = None) -> list[Key]:
        keys = [Key(k["kid"], k["key"], k["type"]) for k in self._session(session_id)["keys"]]
        return [k for k in keys if k.type == type_] if type_ else keys

    @staticmethod
    def parse_cached_keys(cached_keys_data) -> list[dict]:
        keys = []
        if isinstance(cached_keys_data, list):
            for entry in cached_keys_data:
                if isinstance(entry, dict) and "kid" in entry and "key" in entry:
                    keys.append({"kid": entry["kid"], "key": entry["key"], "type": "CONTENT"})
        return keys

    @staticmethod
    def parse_keys_response(data: dict) -> list[dict]:
        keys = []
        raw = data.get("keys")
        if isinstance(raw, str):
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("--key ") and ":" in line[6:]:
                    kid, key = line[6:].split(":", 1)
                    keys.append({"kid": kid.strip(), "key": key.strip(), "type": "CONTENT"})
        elif isinstance(raw, list):
            for entry in raw:
                keys.append({"kid": entry.get("kid"), "key": entry.get("key"), "type": entry.get("type", "CONTENT")})
        return keys


def build_remote_cdm(cfg: dict, system: str):
    """
    Build the remote CDM described by a `DRM.widevine` / `DRM.playready` config dict.
    """
    cfg = dict(cfg)
    backend = str(cfg.pop("type", "")).lower()

    if backend == "decrypt_labs":
        if system == "playready":
            cfg.setdefault("device_type", "PLAYREADY")
        return DecryptLabsRemoteCDM(**cfg)

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
