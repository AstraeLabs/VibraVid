# 09.06.24

import base64
import re


def normalize_kid(kid) -> str:
    """Canonical KID/KEY hex form: dash-stripped, whitespace-trimmed, lowercase."""
    return str(kid if kid is not None else "").replace("-", "").strip().lower()


def accumulate_content_key(keys_list: list, extracted_kids: set, kid_raw, key_raw) -> None:
    """Format a CDM key as ``"kid:key"``. Drops all-zero keys -- CDMs (Widevine/PlayReady)"""
    key = normalize_kid(key_raw)
    if not key or set(key) == {"0"}:
        return
    
    kid = normalize_kid(kid_raw)
    formatted = f"{kid}:{key}"
    if formatted not in keys_list:
        keys_list.append(formatted)
        extracted_kids.add(kid)


_SYSTEMS_DATA = {
    "WIDEVINE": ("edef8ba979d64acea3c827dcd51d21ed", "Widevine", "WV"),
    "PLAYREADY": ("9a04f07998404286ab92e65be0885f95", "PlayReady", "PR"),
    "FAIRPLAY": ("94ce86fb07ff4f43adb893d2fa968ca2", "FairPlay", "FP"),
    "PRIMETIME": ("f239e769efa348509c16a903c6932efb", "Adobe Primetime", "PT"),
    "NAGRA": ("adb41c242dbf4a6d958b4457c0d27b95", "Nagra", "NA"),
    "CLEARKEY": ("1077efecc0b24d02ace33c1e52e2fb4b", "ClearKey", "CK"),
    "CLEARKEY_V1": ("e2719d58a985b3c9781ab030af78d30e", "ClearKey (v0.1)", "CK"),
    "IRDETO": ("644fe7b5260f4fad949a0762ffd8b0f4", "Irdeto", "IR"),
    "CMLA": ("69f908af481646ea910ccd5dcccb0a3a", "CMLA (OMA DRM)", "CM"),
    "ITTIAM": ("80a6be7e14484c379e70d5aebe04c8d2", "Ittiam", "IT"),
    "SECUREMEDIA": ("3ea8778f77424bf9b18be834b2356d01", "SecureMedia", "SM"),
    "MARLIN": ("5e629af538da4332a621485ea588cb3f", "Marlin", "ML"),
    "MARLIN_MSB": ("29701fe43cc74a348c5bae90c7439a47", "Marlin", "ML"),
}


class _DRMSystems(dict):
    WIDEVINE = _SYSTEMS_DATA["WIDEVINE"][0]
    PLAYREADY = _SYSTEMS_DATA["PLAYREADY"][0]
    FAIRPLAY = _SYSTEMS_DATA["FAIRPLAY"][0]
    PRIMETIME = _SYSTEMS_DATA["PRIMETIME"][0]
    NAGRA = _SYSTEMS_DATA["NAGRA"][0]
    CLEARKEY = _SYSTEMS_DATA["CLEARKEY"][0]
    CLEARKEY_V1 = _SYSTEMS_DATA["CLEARKEY_V1"][0]
    IRDETO = _SYSTEMS_DATA["IRDETO"][0]
    CMLA = _SYSTEMS_DATA["CMLA"][0]
    ITTIAM = _SYSTEMS_DATA["ITTIAM"][0]
    SECUREMEDIA = _SYSTEMS_DATA["SECUREMEDIA"][0]
    MARLIN = _SYSTEMS_DATA["MARLIN"][0]
    MARLIN_MSB = _SYSTEMS_DATA["MARLIN_MSB"][0]

    def __init__(self):
        super().__init__((name, data[0]) for name, data in _SYSTEMS_DATA.items())

    def __getattr__(self, name):
        if name in _SYSTEMS_DATA:
            return _SYSTEMS_DATA[name][0]
        raise AttributeError(f"Unknown DRM system: {name}")

    @staticmethod
    def to_system_id(hex_id: str) -> str:
        """Convert 32-char hex to dashed UUID format (8-4-4-4-12)."""
        h = normalize_kid(hex_id)
        if len(h) != 32:
            raise ValueError(f"Expected 32 hex chars, got {len(h)}: {hex_id!r}")
        return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    @staticmethod
    def norm_system_id(raw: str) -> str:
        """Normalize GUID/SystemID to lowercase dashed form, handling Microsoft GUID syntax."""
        s = (raw or "").lower().replace("{", "").replace("}", "").strip()
        s = s.replace("-", "")
        if len(s) != 32:
            return raw
        return f"{s[0:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:32]}"

    @staticmethod
    def extract_kid_from_playready_pro(pro_b64: str):
        """Extract KID from base64 PlayReady Object (PRO) or WRM Header."""
        try:
            data = base64.b64decode(pro_b64)
        except Exception:
            return None

        try:
            if len(data) < 6:
                return None

            offset = 6
            while offset + 4 <= len(data):
                rec_type = int.from_bytes(data[offset : offset + 2], "little")
                rec_len = int.from_bytes(data[offset + 2 : offset + 4], "little")
                offset += 4
                if rec_len <= 0 or offset + rec_len > len(data):
                    break

                if rec_type == 0x0001:
                    wrm_xml = data[offset : offset + rec_len].decode("utf-16-le", errors="ignore")

                    m = re.search(r"<KID[^>]*>([A-Za-z0-9+/=]+)</KID>", wrm_xml)
                    if m:
                        kid_b64 = m.group(1).strip()
                        kid_bytes = base64.b64decode(kid_b64)
                        if len(kid_bytes) == 16:
                            b = kid_bytes
                            return (
                                f"{b[3]:02x}{b[2]:02x}{b[1]:02x}{b[0]:02x}"
                                f"{b[5]:02x}{b[4]:02x}{b[7]:02x}{b[6]:02x}" + b[8:16].hex()
                            )

                    m = re.search(r'KeyID="([^"]+)"', wrm_xml)
                    if m:
                        return m.group(1).strip()

                    m = re.search(r'VALUE="([A-Za-z0-9+/=]+)"', wrm_xml)
                    if m:
                        kid_b64 = m.group(1)
                        kid_bytes = base64.b64decode(kid_b64 + "==")
                        if len(kid_bytes) >= 16:
                            return (
                                kid_bytes[3::-1].hex()
                                + kid_bytes[5:3:-1].hex()
                                + kid_bytes[7:5:-1].hex()
                                + kid_bytes[8:10].hex()
                                + kid_bytes[10:16].hex()
                            )

                offset += rec_len

        except Exception:
            pass

        return None

    @staticmethod
    def build_widevine_pssh_from_kid(kid_hex: str) -> str:
        """Synthesize minimal Widevine v0 PSSH box from KID hex."""
        from uuid import UUID

        from pywidevine.pssh import PSSH

        kid = normalize_kid(kid_hex)
        pssh = PSSH.new(system_id=PSSH.SystemId.Widevine, key_ids=[UUID(bytes=bytes.fromhex(kid))])
        return pssh.dumps()


KNOWN_DRM_SYSTEMS = {data[0]: data[1] for data in _SYSTEMS_DATA.values()}


class DRMType:
    WIDEVINE = "WV"
    PLAYREADY = "PR"
    FAIRPLAY = "FP"
    PRIMETIME = "PT"
    NAGRA = "NA"
    CLEARKEY = "CK"
    IRDETO = "IR"
    CMLA = "CM"
    ITTIAM = "IT"
    SECUREMEDIA = "SM"
    MARLIN = "ML"
    UNKNOWN = "UNK"

    ALL = ("WV", "PR", "FP", "PT", "NA", "CK", "IR", "CM", "IT", "SM", "ML", "UNK")
    KNOWN = ("WV", "PR", "FP", "PT", "NA", "CK", "IR", "CM", "IT", "SM", "ML")
    HEX_TO_CODE = {data[0]: data[2] for data in _SYSTEMS_DATA.values()}

    DISPLAY = {data[2]: data[1] for data in _SYSTEMS_DATA.values()}
    DISPLAY["UNK"] = "Unknown"

    @classmethod
    def from_scheme(cls, scheme) -> str:
        """Classify a DRM system from any scheme string"""
        s = (scheme or "").lower()
        if not s:
            return cls.UNKNOWN

        sd = s.replace("-", "")
        if "widevine" in s or "edef8ba9" in sd:
            return cls.WIDEVINE

        if "playready" in s or "com.microsoft" in s or "9a04f079" in sd:
            return cls.PLAYREADY

        if "fairplay" in s or "com.apple" in s or "streamingkeydelivery" in s or "94ce86fb" in sd:
            return cls.FAIRPLAY

        if "clearkey" in s or "org.w3.clearkey" in s or "1077efec" in sd:
            return cls.CLEARKEY

        if "e2719d58" in sd:
            return cls.CLEARKEY
        
        return cls.UNKNOWN

    @classmethod
    def display(cls, code: str) -> str:
        """Return human-readable name for short code."""
        return cls.DISPLAY.get(code.upper(), "Unknown")
