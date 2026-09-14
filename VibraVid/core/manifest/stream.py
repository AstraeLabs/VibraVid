# 13.03.26

import base64
import logging
from dataclasses import dataclass, field

from VibraVid.core.drm.system import DRMType, _DRMSystems


def track_label(s) -> str:
    """Build a short human-readable label for a stream/track (DRM reporting, decrypt-failure logs)."""
    stype = getattr(s, "type", "") or ""

    if stype == "video":
        h = getattr(s, "height", 0) or 0
        if not h:
            res = getattr(s, "resolution", "") or ""
            parts = res.lower().replace("p", "").split("x")
            try:
                h = int(parts[-1])
            except (ValueError, IndexError):
                h = 0
        return f"video {h}p" if h else "video"

    if stype == "audio":
        lang = (getattr(s, "language", "") or "").strip()
        if lang and lang.lower() not in ("und", "n/a", ""):
            return f"audio {lang.upper()}"
        return "audio"

    return stype or "stream"


logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Compact human-readable duration, e.g. '1h02m', '3m20s', '45s'."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class DRMInfo:
    WIDEVINE_SYSTEM_ID = _DRMSystems.to_system_id(_DRMSystems.WIDEVINE)
    PLAYREADY_SYSTEM_ID = _DRMSystems.to_system_id(_DRMSystems.PLAYREADY)
    FAIRPLAY_SYSTEM_ID = _DRMSystems.to_system_id(_DRMSystems.FAIRPLAY)
    CLEARKEY_SYSTEM_ID = _DRMSystems.to_system_id(_DRMSystems.CLEARKEY)

    def __init__(self):
        self.pssh = None
        self.kid = None
        self.key = None
        self.system_id = None
        self.drm_type = None
        self.default_kid = None
        self.default_kids: list[str] = []
        self.method = None
        self._pssh_by_type: dict[str, str] = {}
        self._all_pssh_by_type: dict[str, list[str]] = {}
        self._drm_types: list[str] = []
        self._key_uri_by_type: dict[str, str] = {}
        self._key_uri_by_pssh: dict[str, str] = {}
        self.display_type: str | None = None

    def _record_pssh(self, drm_type: str, pssh_base64: str) -> None:
        bucket = self._all_pssh_by_type.setdefault(drm_type, [])
        if pssh_base64 not in bucket:
            bucket.append(pssh_base64)

    def _record_key_uri(self, drm_type: str, pssh_base64: str, key_uri: str | None) -> None:
        if not key_uri:
            return
        self._key_uri_by_type[drm_type] = key_uri
        self._key_uri_by_pssh[pssh_base64] = key_uri

    def set_pssh(self, pssh_base64: str, drm_type_hint: str = None, key_uri: str = None) -> None:
        """Register a PSSH (or FairPlay SKD URI) for a DRM system."""
        detected: str | None = None

        # FairPlay SKD URI is not a base64 PSSH box; keep it as opaque value.
        if isinstance(pssh_base64, str) and pssh_base64.lower().startswith("skd:"):
            detected = (drm_type_hint or DRMType.FAIRPLAY).upper()
            self._pssh_by_type[detected] = pssh_base64
            self._record_pssh(detected, pssh_base64)
            self._record_key_uri(detected, pssh_base64, key_uri or pssh_base64)
            if detected not in self._drm_types:
                self._drm_types.append(detected)
            self.pssh = pssh_base64
            self.drm_type = detected
            return

        try:
            from uuid import UUID as _UUID

            from pywidevine.pssh import PSSH as WV_PSSH

            _WV_UUID = _UUID(hex=_DRMSystems.WIDEVINE)
            _PR_UUID = _UUID(hex=_DRMSystems.PLAYREADY)
            _FP_UUID = _UUID(hex=_DRMSystems.FAIRPLAY)
            _CK_UUID = _UUID(hex=_DRMSystems.CLEARKEY)

            wv_obj = WV_PSSH(pssh_base64)
            sid = wv_obj.system_id
            self.system_id = str(sid)

            if sid == _WV_UUID:
                detected = DRMType.WIDEVINE
            elif sid == _PR_UUID:
                detected = DRMType.PLAYREADY
            elif sid == _FP_UUID:
                detected = DRMType.FAIRPLAY
            elif sid == _CK_UUID:
                detected = DRMType.CLEARKEY
            else:
                detected = DRMType.UNKNOWN

        except ImportError:
            pass
        except Exception as exc:
            logger.error(f"DRMInfo.set_pssh [pywidevine] error: {exc}")

        if (not detected or detected == DRMType.UNKNOWN) and (
            (drm_type_hint or "").upper() == DRMType.PLAYREADY or (self.drm_type or "") == DRMType.PLAYREADY
        ):
            try:
                from pyplayready.system.pssh import PSSH as PR_PSSH

                PR_PSSH(pssh_base64)
                detected = DRMType.PLAYREADY
            except ImportError:
                pass
            except Exception as exc:
                logger.error(f"DRMInfo.set_pssh [pyplayready] error: {exc}")

        if not detected or detected == DRMType.UNKNOWN:
            try:
                data = base64.b64decode(pssh_base64)
                if len(data) >= 28 and data[4:8] == b"pssh":
                    sid_bytes = data[12:28]
                    sid_str = "-".join(
                        [
                            sid_bytes[0:4].hex(),
                            sid_bytes[4:6].hex(),
                            sid_bytes[6:8].hex(),
                            sid_bytes[8:10].hex(),
                            sid_bytes[10:16].hex(),
                        ]
                    )

                    self.system_id = sid_str
                    sid_lo = sid_str.lower()
                    if sid_lo == self.WIDEVINE_SYSTEM_ID:
                        detected = DRMType.WIDEVINE
                    elif sid_lo == self.PLAYREADY_SYSTEM_ID:
                        detected = DRMType.PLAYREADY
                    elif sid_lo == self.FAIRPLAY_SYSTEM_ID:
                        detected = DRMType.FAIRPLAY
                    elif sid_lo == self.CLEARKEY_SYSTEM_ID:
                        detected = DRMType.CLEARKEY
                    else:
                        detected = DRMType.UNKNOWN

            except Exception as exc:
                logger.error(f"DRMInfo.set_pssh [manual] error: {exc}")

        if not detected or detected == DRMType.UNKNOWN:
            if drm_type_hint:
                detected = drm_type_hint.upper()
            elif self.drm_type and self.drm_type != DRMType.UNKNOWN:
                detected = self.drm_type
            else:
                detected = DRMType.UNKNOWN

        self._pssh_by_type[detected] = pssh_base64
        self._record_pssh(detected, pssh_base64)
        self._record_key_uri(detected, pssh_base64, key_uri)
        if detected not in self._drm_types:
            self._drm_types.append(detected)

        for pref in (DRMType.WIDEVINE, DRMType.PLAYREADY, DRMType.FAIRPLAY, DRMType.CLEARKEY, DRMType.UNKNOWN):
            if pref in self._pssh_by_type:
                self.pssh = self._pssh_by_type[pref]
                self.drm_type = pref
                break

    def get_pssh_for(self, drm_type: str) -> str | None:
        """Return the PSSH (or FairPlay SKD URI) for *drm_type*, or None if not present."""
        return self._pssh_by_type.get(drm_type.upper())

    def get_all_pssh_for(self, drm_type: str) -> list[str]:
        """All distinct PSSH variants for *drm_type*, in document order (may be empty)."""
        return list(self._all_pssh_by_type.get(drm_type.upper(), []))

    def get_all_drm_types(self) -> list[str]:
        """All DRM types seen in this manifest, in document order (may be empty)."""
        return list(self._drm_types)

    def get_key_uri(self, drm_type: str = None, pssh: str = None) -> str | None:
        """The verbatim ``#EXT-X-KEY`` URI a PSSH was decoded from, query string intact."""
        if pssh and pssh in self._key_uri_by_pssh:
            return self._key_uri_by_pssh[pssh]
        return self._key_uri_by_type.get((drm_type or self.drm_type or "").upper())

    def to_dict(self) -> dict:
        """Canonical DRM dict consumed by the HLS/ISM downloader fallbacks::

        {'widevine': [{'pssh','type','kid','key_uri'}], 'playready': [...], 'fairplay': [{'uri',...}]}
        """
        result: dict = {"widevine": [], "playready": [], "fairplay": []}
        pssh_wv = self.get_pssh_for(DRMType.WIDEVINE)
        if pssh_wv:
            result["widevine"].append(
                {
                    "pssh": pssh_wv,
                    "type": "Widevine",
                    "kid": self.kid,
                    "key_uri": self.get_key_uri(DRMType.WIDEVINE, pssh_wv),
                }
            )
        pssh_pr = self.get_pssh_for(DRMType.PLAYREADY)
        if pssh_pr:
            result["playready"].append(
                {
                    "pssh": pssh_pr,
                    "type": "PlayReady",
                    "kid": self.kid,
                    "key_uri": self.get_key_uri(DRMType.PLAYREADY, pssh_pr),
                }
            )
        pssh_fp = self.get_pssh_for(DRMType.FAIRPLAY)
        if pssh_fp:
            result["fairplay"].append(
                {
                    "uri": pssh_fp,
                    "type": "FairPlay",
                    "kid": self.kid,
                    "key_uri": self.get_key_uri(DRMType.FAIRPLAY, pssh_fp),
                }
            )
        return result

    def add_advertised_type(self, drm_type: str) -> None:
        """Register a DRM system merely *advertised* by the manifest (e.g. HLS ``ALLOWED-CPC``) without an associated PSSH/KID."""
        if drm_type and drm_type not in self._drm_types:
            self._drm_types.append(drm_type)

    def set_kid(self, kid_hex: str) -> None:
        kid = (kid_hex or "").lower().replace("-", "").strip()
        if not kid:
            return

        if kid not in self.default_kids:
            self.default_kids.append(kid)

        if not self.kid:
            self.kid = kid
        if not self.default_kid:
            self.default_kid = kid

    def get_all_kids(self) -> list[str]:
        kids = list(self.default_kids)
        if not kids:
            for candidate in (self.kid, self.default_kid):
                if candidate and candidate not in kids:
                    kids.append(candidate)
        return kids

    def set_key(self, key_hex: str) -> None:
        self.key = key_hex.lower().replace("-", "")

    def set_method(self, scheme_id_uri: str) -> None:
        if not scheme_id_uri:
            return

        s = scheme_id_uri.lower()
        if "cbcs" in s:
            self.method = "cbcs"
        elif "cenc" in s or "mp4protection" in s:
            self.method = "cenc"
        else:
            self.method = scheme_id_uri.split(":")[-1] if ":" in scheme_id_uri else scheme_id_uri

        detected = DRMType.from_scheme(scheme_id_uri)
        if detected and detected != DRMType.UNKNOWN:
            if detected not in self._drm_types:
                self._drm_types.append(detected)

            if not self.drm_type:
                self.drm_type = detected

    def is_encrypted(self) -> bool:
        return bool(self.pssh or self.kid or self.default_kid or self.default_kids or self._pssh_by_type)

    def get_drm_display(self) -> str:
        if self._drm_types:
            return "+".join(self._drm_types)

        if self.drm_type:
            return self.drm_type

        if self.display_type:
            return self.display_type

        if self.default_kids or self.default_kid:
            return "UNK"

        return "-"

    def get_kid_display(self) -> str:
        """Full KID for one-liner logging, e.g. 'a1b2c3d4...deadbeef'. Empty if no KID is known."""
        kid = self.kid or self.default_kid or (self.default_kids[0] if self.default_kids else "")
        return f"{kid}" if kid else ""

    def __repr__(self) -> str:
        if not self.is_encrypted():
            return "DRMInfo(plain)"

        types = "+".join(self._drm_types) if self._drm_types else (self.drm_type or "?")
        return f"DRMInfo({types}, KID={self.get_kid_display()})"


@dataclass
class Segment:
    url: str
    number: int
    seg_type: str = "media"
    size: int = 0
    downloaded: bool = False
    byte_range: str = ""  # e.g. "12132-31195" for byte-range requests
    duration: float = 0.0  # seconds; set by parser when known (e.g. #EXTINF or <S d=…/>)
    estimated_size: int = 0  # bytes; computed from stream bitrate × duration when size == 0
    period_idx: int = 0  # DASH Period this segment belongs to (multi-period manifests)
    encrypted: bool = False  # True when this segment's Period is DRM-protected
    inline_data: bytes | None = None  # segment bytes carried by the manifest itself

    def __repr__(self) -> str:
        dur_s = f", {self.duration:.3f}s" if self.duration else ""
        return f"Segment({self.number}, {self.seg_type}{dur_s})"


@dataclass
class Stream:
    """
    Unified stream descriptor for both HLS and DASH content.
    """

    type: str

    id: str = ""
    resolution: str = ""
    width: int = 0
    height: int = 0
    fps: str = ""
    bitrate: int = 0
    avg_bitrate: int = 0
    codecs: str = ""
    language: str = "und"
    resolved_language: str = ""
    name: str = ""
    channels: str = ""
    role: str = "main"

    # Video-only
    video_range: str = ""
    hdcp_level: str = ""
    scan_type: str = ""

    # Audio-only
    default: bool = False
    autoselect: bool = False
    assoc_language: str = ""
    sample_rate: int = 0

    # Subtitle / Audio flags
    forced: bool = False
    is_cc: bool = False
    is_sdh: bool = False

    # All types
    label: str = ""
    accessibility: str = ""

    drm: DRMInfo = field(default_factory=DRMInfo)

    key_data: bytes | None = None
    iv: str | None = None

    # Smooth Streaming / ISM: raw CodecPrivateData (set by manifest/ism.py, consumed by velora/_ism_postproc.py)
    codec_private_data: bytes | None = None

    playlist_url: str | None = None
    segments: list[Segment] = field(default_factory=list)

    selected: bool = False
    duration: float = 0.0
    format: str = ""
    is_external: bool = False
    supports_live_decryption: bool = True  # Default True, parsers will set False when needed
    is_live: bool = False
    is_wvtt_mp4: bool = False

    # ── Size estimation ───────────────────────────────────────────────────────
    # Populated by compute_estimated_size(); do not set manually.
    estimated_size: int = 0  # bytes; total stream size estimate

    def add_segment(self, seg: Segment) -> None:
        self.segments.append(seg)

    def compute_estimated_size(self, bitrate_override: int = 0) -> int:
        """
        Compute and store an estimated total size in bytes, then return it.

        Priority order:
        1. Sum of real ``Segment.size`` values (when already downloaded/known).
        2. Sum of ``Segment.estimated_size`` values (pre-filled by parser from
           per-segment durations × bitrate, e.g. from DASH <S d=…/> or
           HLS #EXTINF).
        3. Fallback: ``(avg_bitrate or bitrate or bitrate_override) × duration``
           using the stream-level duration.

        The result is stored in ``self.estimated_size`` and also returned.
        """
        # 1. Real sizes from already-downloaded segments
        real_total = sum(s.size for s in self.segments if s.size)
        if real_total:
            self.estimated_size = real_total
            return self.estimated_size

        # 2. Per-segment estimates (set by parser from per-segment duration × bitrate)
        seg_est_total = sum(s.estimated_size for s in self.segments if s.estimated_size)
        if seg_est_total:
            self.estimated_size = seg_est_total
            return self.estimated_size

        # 3. Stream-level fallback: bitrate × total duration
        bw = bitrate_override or self.avg_bitrate or self.bitrate
        if bw and self.duration > 0:
            self.estimated_size = int(bw / 8 * self.duration)
            return self.estimated_size

        self.estimated_size = 0
        return 0

    # ─── Display helpers ───────────────────────────────────────────────────────
    @property
    def bitrate_display(self) -> str:
        bw = self.bitrate
        if bw >= 1_000_000:
            return f"{bw / 1e6:.1f} Mbps"
        if bw >= 1_000:
            return f"{bw / 1e3:.0f} Kbps"
        return f"{bw} bps" if bw else "N/A"

    @property
    def avg_bitrate_display(self) -> str:
        bw = self.avg_bitrate
        if not bw:
            return ""
        if bw >= 1_000_000:
            return f"~{bw / 1e6:.1f} Mbps"
        if bw >= 1_000:
            return f"~{bw / 1e3:.0f} Kbps"
        return f"~{bw} bps"

    @property
    def fps_float(self) -> float:
        try:
            if "/" in str(self.fps):
                num, den = self.fps.split("/")
                return round(float(num) / float(den), 3)
            return float(self.fps)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    def get_type_display(self) -> str:
        base = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle", "image": "Thumbnail"}.get(
            self.type, self.type.capitalize()
        )
        return f"{base} *EXT" if self.is_external else base

    def get_short_codec(self) -> str:
        from VibraVid.core.utils.codec import get_short_codec as _gsc

        return _gsc(self.type, self.codecs)

    def get_channel_label(self) -> str:
        from VibraVid.core.utils.codec import get_channel_label as _gcl

        return _gcl(self.channels) if self.channels else ""

    def get_hdr_display(self) -> str:
        vr = (self.video_range or "").upper()
        return vr if vr and vr != "SDR" else ""

    @property
    def encryption_method(self) -> str | None:
        """Encryption mode (cenc/cbcs/SAMPLE-AES…), derived from the attached DRMInfo."""
        return self.drm.method if self.drm else None

    def get_flags_display(self) -> str:
        flags = []
        if self.forced:
            flags.append("Forced")
        if self.is_cc:
            flags.append("CC")
        if self.is_sdh:
            flags.append("SDH")
        if self.default:
            flags.append("Default")
        return f"[{', '.join(flags)}]" if flags else ""

    def __str__(self) -> str:
        """
        Human-readable one-liner for logger.info and debug output.

        Examples:
            Stream(video | id='vid:1920x1080@4500000' | 1920x1080 | 4.5 Mbps | H.264 | SDR)
            Stream(audio | id='audio:ita' | it-IT | 128 Kbps | AAC | Stereo | [Default])
            Stream(subtitle | id='subs:ita-forced' | it-IT | VTT | [Forced])
        """
        try:
            codec = self.get_short_codec() or self.codecs or None
            lang = self.resolved_language or self.language or None
            flags = self.get_flags_display() or None
            drm = self.drm.get_drm_display() if self.drm.is_encrypted() else None
            id_s = f"id={self.id!r}" if self.id else None
            
            seg_count = len(self.segments)
            segs_s = f"segs={seg_count}" if seg_count else None
            dur_s = f"~{format_duration(self.duration)}" if self.bitrate and self.duration > 0 else None
            size_s = None
            if self.bitrate and self.duration > 0:
                from VibraVid.core.velora.util.formatting import format_size as _format_size
                est_size = self.compute_estimated_size()
                if est_size:
                    size_s = f"~{_format_size(est_size)}"
            
            kid_s = f"KID={self.drm.get_kid_display()}" if self.drm.is_encrypted() and self.drm.get_kid_display() else None

            if self.type == "video":
                fps_s = f"{self.fps_float:.0f}fps" if self.fps_float else None
                vrange = self.video_range if self.video_range and self.video_range != "SDR" else None
                avg_s = self.avg_bitrate_display or None
                scan_s = self.scan_type if self.scan_type and self.scan_type != "progressive" else None
                parts = [
                    id_s,
                    self.resolution or None,
                    self.bitrate_display if self.bitrate else None,
                    avg_s,
                    codec,
                    fps_s,
                    vrange,
                    scan_s,
                    segs_s,
                    dur_s,
                    size_s,
                    drm,
                    kid_s,
                ]

            elif self.type == "audio":
                ch = self.get_channel_label() or (self.channels if self.channels else None)
                sr = f"{self.sample_rate}Hz" if self.sample_rate else None
                parts = [
                    id_s, lang, self.bitrate_display if self.bitrate else None, codec, ch, sr, flags,
                    segs_s, dur_s, size_s, drm, kid_s,
                ]

            else:  # subtitle
                wvtt_tag = "wvtt-mp4" if self.is_wvtt_mp4 else None
                parts = [id_s, lang, codec, wvtt_tag, flags, segs_s, dur_s, size_s, drm, kid_s]

            filtered = [p for p in parts if p]
            return f"Stream({self.type} | {' | '.join(filtered)})"

        except Exception:
            # Fallback: never raise in __str__
            return f"Stream({self.type}, lang={self.language}, bw={self.bitrate})"

    def __repr__(self) -> str:
        drm_s = f", {self.drm.get_drm_display()}" if self.drm.is_encrypted() else ""
        hdr_s = f", {self.video_range}" if self.video_range and self.video_range != "SDR" else ""
        lang_s = self.resolved_language or self.language

        if self.type == "video":
            return f"Stream(video, {self.resolution}{hdr_s}, {self.bitrate_display}{drm_s})"

        if self.type == "audio":
            flags = self.get_flags_display()
            return f"Stream(audio, {lang_s}{f' {flags}' if flags else ''}, {self.bitrate_display}{drm_s})"

        flags = self.get_flags_display()
        wvtt_s = " [wvtt-mp4]" if self.is_wvtt_mp4 else ""
        return f"Stream({self.type}, {lang_s}{f' {flags}' if flags else ''}{wvtt_s})"
