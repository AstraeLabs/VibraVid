# 03.05.26

import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console

from VibraVid.core.drm.system import DRMType, _DRMSystems
from VibraVid.core.manifest._utils import fast_urljoin, is_simple_relative_ref, save_raw_manifest
from VibraVid.core.manifest.stream import DRMInfo, Segment, Stream
from VibraVid.core.utils.codec import infer_video_range
from VibraVid.core.utils.language import resolve_locale
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

console = Console()
logger = logging.getLogger(__name__)

_PLAYREADY_SYSTEM_ID = _DRMSystems.to_system_id(_DRMSystems.PLAYREADY)
_WIDEVINE_SYSTEM_ID = _DRMSystems.to_system_id(_DRMSystems.WIDEVINE)

_BITRATE_RE = re.compile(r"\{[Bb]itrate\}")
_STARTTIME_RE = re.compile(r"\{start[ _][Tt]ime\}|\{[Ss]tart[Tt]ime\}")
_TECHNICAL_STREAM_NAME_RE = re.compile(r"_(audio|video|subtitle)_|_\d+k?$|^(text|audio|video|subtitle|track|stream)[-_]?\d+$", re.IGNORECASE)

_FOURCC_TO_CODEC: dict[str, str] = {
    "h264": "avc1",
    "avc1": "avc1",
    "avc ": "avc1",
    "hevc": "hvc1",
    "hvc1": "hvc1",
    "hev1": "hvc1",
    "aacl": "mp4a.40.2",
    "aach": "mp4a.40.5",
    "aacp": "mp4a.40.29",
    "ec-3": "ec-3",
    "ac-3": "ac-3",
    "wma2": "wma",
    "wmap": "wmap",
    "ttml": "ttml",
    "dfxp": "ttml",
}


def _fourcc_to_codec(fourcc: str) -> str:
    return _FOURCC_TO_CODEC.get((fourcc or "").lower(), (fourcc or "").lower())


def _is_technical_stream_name(name: str) -> bool:
    return bool(name) and bool(_TECHNICAL_STREAM_NAME_RE.search(name.lower()))


class ISMParser:
    def __init__(self, ism_url: str, headers: dict[str, str] | None = None, content: str | None = None):
        self.ism_url = ism_url
        self.headers = headers or {}
        self._injected = content
        self.raw_content: str | None = content
        self._root: ET.Element | None = None
        self._base_url = self._calc_base_url(ism_url)
        self._timescale: int = 10_000_000  # ISM default: 100-nanosecond ticks
        self._manifest_is_live: bool = False

    @staticmethod
    def _calc_base_url(url: str) -> str:
        """
        Strip the trailing ``/manifest`` (case-insensitive) path component and
        return a base URL with a trailing slash suitable for urljoin.

        Examples::

            .../video.ism/manifest  →  .../video.ism/
            .../video.ism           →  .../video.ism/
        """
        p = urlparse(url)
        path = re.sub(r"/[Mm]anifest$", "", p.path)
        if not path.endswith("/"):
            path += "/"
        return f"{p.scheme}://{p.netloc}{path}"

    @staticmethod
    def _parse_xml(raw: bytes | str) -> "tuple[ET.Element, str]":
        """Parse Smooth Streaming XML, honouring the ``<?xml encoding=...?>`` declaration / BOM."""
        if isinstance(raw, str):
            data = raw.encode("utf-8")
        else:
            data = raw
        
        root = ET.fromstring(data)  # bytes -> encoding taken from the XML decl / BOM
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        else:
            text = data.decode("utf-8", errors="replace")
        return root, text

    def fetch_manifest(self) -> bool:
        """Fetch (or use injected) manifest XML and parse it into ``self._root``."""
        start_parsing_time = time.time()

        if self._injected:
            try:
                self._root, self.raw_content = self._parse_xml(self._injected)
                logger.info(f"ISMParser: injected XML parsed in {time.time() - start_parsing_time:.2f}s")
                return True
            except ET.ParseError as exc:
                logger.error(f"ISMParser: injected XML parse error: {exc}")
                self._injected = None  # fall through to network fetch

        if self.ism_url.startswith("file://"):
            try:
                from urllib.request import url2pathname

                local_path = Path(url2pathname(urlparse(self.ism_url).path))
                self._base_url = local_path.parent.as_uri() + "/"
                self._root, self.raw_content = self._parse_xml(local_path.read_bytes())
                logger.info(f"ISMParser: local ISM manifest in {time.time() - start_parsing_time:.2f}s")
                return True
            except Exception as exc:
                console.print(f"[red]Failed to read local ISM manifest: {exc}.")
                logger.error(f"ISMParser: local file read failed: {exc}")
                return False

        try:
            timeout = config_manager.config.get_int("REQUESTS", "timeout")
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
                r = c.get(self.ism_url)
                r.raise_for_status()
                content = r.content
                effective_url = str(r.url)

            # The manifest host may 302 to a session/edge-specific CDN node (e.g.
            # a load-balancer redirecting to "ecNN-....cdn...pl") -- fragment URLs
            # must be resolved against that final host, not the pre-redirect one.
            if effective_url and effective_url != self.ism_url:
                self._base_url = self._calc_base_url(effective_url)

            self._root, self.raw_content = self._parse_xml(content)
            logger.info(f"ISMParser: fetched and parsed ISM in {time.time() - start_parsing_time:.2f}s")
            return True
        except Exception as exc:
            console.print(f"[red]Error fetching/parsing ISM manifest: {exc}[/red]")
            logger.error(f"ISMParser: fetch/parse failed: {exc}")
            return False

    def save_raw(self, directory: Path) -> Path:
        return save_raw_manifest(self.raw_content, directory, "raw.ism")

    def parse_streams(self) -> list[Stream]:
        """
        Parse the ISM manifest into a flat list of :class:`Stream` objects.

        One ``Stream`` is created per ``<QualityLevel>`` element.  All streams
        within a ``<StreamIndex>`` share the same chunk timeline expanded from
        ``<c>`` elements.
        """
        if self._root is None:
            return []

        root = self._root
        streams: list[Stream] = []

        # ── Global metadata ───────────────────────────────────────────────────
        self._timescale = int(root.get("TimeScale", "10000000") or "10000000")
        duration_ticks = int(root.get("Duration", "0") or "0")
        global_duration = (duration_ticks / self._timescale) if self._timescale > 0 else 0.0

        is_live_attr = (root.get("IsLive") or "FALSE").strip().upper()
        self._manifest_is_live = is_live_attr == "TRUE"

        # ── Global DRM ────────────────────────────────────────────────────────
        global_drm = self._extract_drm(root)

        # ── StreamIndex elements ──────────────────────────────────────────────
        for si in root.findall("StreamIndex"):
            si_type_raw = (si.get("Type") or "").lower()

            if si_type_raw == "video":
                stype = "video"
            elif si_type_raw == "audio":
                stype = "audio"
            elif si_type_raw in ("text", "textstream", "subtitle"):
                stype = "subtitle"
            elif si_type_raw == "image":
                logger.info("ISMParser: skipping image StreamIndex")
                continue
            else:
                logger.info(f"ISMParser: unknown StreamIndex Type={si_type_raw!r} — skipping")
                continue

            url_template = si.get("Url") or si.get("url") or ""
            lang_raw = si.get("Language") or si.get("language") or ""
            si_name = si.get("Name") or si.get("name") or ""
            default_lang = (si.get("DefaultLanguage") or "").strip().lower()

            # Chunk timeline shared across all QualityLevels in this StreamIndex
            timeline = self._parse_chunk_timeline(si, global_duration)
            if not timeline:
                logger.warning(f"ISMParser: empty chunk timeline for {stype} StreamIndex — skipping")
                continue

            for ql in si.findall("QualityLevel"):
                s = self._parse_quality_level(
                    ql, stype, lang_raw, si_name, default_lang, global_duration, url_template, timeline, global_drm
                )
                if s is not None:
                    streams.append(s)
                    logger.info(f"{s}")

        if self._manifest_is_live:
            for s in streams:
                s.is_live = True

        logger.info(f"ISM manifest type: {'LIVE' if self._manifest_is_live else 'VOD'} | streams={len(streams)}")
        return streams

    def _parse_chunk_timeline(self, si_element, global_duration: float) -> list[int]:
        """
        Expand ``<c>`` elements into a list of absolute start times (in timescale ticks).
        """
        timeline: list[int] = []
        current_time: int = 0

        for c in si_element.findall("c"):
            t_attr = c.get("t")
            d_attr = c.get("d")
            r_attr = c.get("r")

            # Explicit absolute timestamp
            if t_attr is not None:
                try:
                    current_time = int(t_attr)
                except ValueError:
                    logger.debug(f"ISMParser: <c t={t_attr!r}> not an int — keeping current_time")

            if d_attr is None:
                logger.debug("ISMParser: <c> element missing 'd' attribute — skipping chunk")
                continue

            try:
                d = int(d_attr)
            except ValueError:
                logger.debug(f"ISMParser: <c d={d_attr!r}> not an int — skipping chunk")
                continue

            repeat = 0
            if r_attr is not None:
                try:
                    repeat = int(r_attr)
                except ValueError:
                    pass

            for _ in range(repeat + 1):
                timeline.append(current_time)
                current_time += d

        return timeline

    def _parse_quality_level(
        self,
        ql,
        stype: str,
        lang_raw: str,
        si_name: str,
        default_lang: str,
        global_duration: float,
        url_template: str,
        timeline: list[int],
        global_drm: DRMInfo,
    ) -> Stream | None:
        """Parse a single ``<QualityLevel>`` into a :class:`Stream`."""
        bitrate = int(ql.get("Bitrate") or ql.get("bitrate") or "0")
        fourcc = ql.get("FourCC") or ql.get("Codec") or ""

        s = Stream(type=stype, format="ism")
        s.bitrate = bitrate
        s.duration = global_duration
        s.is_live = self._manifest_is_live

        # Smooth Streaming/PlayReady protects audio/video sample data only -- subtitle (TTML) StreamIndex entries are never actually encrypted,
        s.drm = global_drm if stype != "subtitle" else DRMInfo()

        # Codec
        s.codecs = _fourcc_to_codec(fourcc) or fourcc
        codec_private_hex = ql.get("CodecPrivateData")
        if codec_private_hex:
            try:
                s.codec_private_data = bytes.fromhex(codec_private_hex)
            except ValueError:
                pass

        # Stable stream ID
        if lang_raw:
            s.id = f"{stype[0]}:{lang_raw}:{bitrate}"
        else:
            s.id = f"{stype[0]}:{bitrate}"

        # ── Type-specific fields ──────────────────────────────────────────────
        if stype == "video":
            self._parse_video_fields(ql, s)

        elif stype == "audio":
            self._parse_audio_fields(ql, s, lang_raw, si_name, default_lang)

        elif stype == "subtitle":
            self._parse_subtitle_fields(ql, s, lang_raw, si_name, fourcc)

        # ── Segments ──────────────────────────────────────────────────────────
        if not url_template:
            logger.warning(f"ISMParser: no URL template for {stype} bitrate={bitrate} — skipping")
            return None

        self._apply_chunk_timeline(s, url_template, bitrate, timeline)

        if not s.segments:
            logger.warning(f"ISMParser: no segments generated for {stype} bitrate={bitrate}")
            return None

        return s

    def _parse_video_fields(self, ql, s: Stream) -> None:
        s.width = int(ql.get("MaxWidth") or ql.get("Width") or "0")
        s.height = int(ql.get("MaxHeight") or ql.get("Height") or "0")
        if s.width and s.height:
            s.resolution = f"{s.width}x{s.height}"

        fr = ql.get("FrameRate") or ""
        if fr:
            s.fps = fr

        # Scan type: ISM does not encode this explicitly — assume progressive
        s.scan_type = "progressive"
        s.video_range = infer_video_range(s.codecs)

    def _parse_audio_fields(self, ql, s: Stream, lang_raw: str, si_name: str, default_lang: str) -> None:
        s.language = lang_raw or "und"
        s.resolved_language = resolve_locale(lang_raw) if lang_raw else ""
        s.name = "" if _is_technical_stream_name(si_name) else (si_name or lang_raw)

        sr = ql.get("SamplingRate") or ql.get("AudioSamplingRate") or ""
        if sr:
            try:
                s.sample_rate = int(sr)
            except ValueError:
                pass

        ch = ql.get("Channels") or ""
        if ch:
            s.channels = ch

        # Mark as default if this language matches the StreamIndex DefaultLanguage
        if lang_raw and default_lang and default_lang == lang_raw.lower():
            s.default = True

    def _parse_subtitle_fields(self, ql, s: Stream, lang_raw: str, si_name: str, fourcc: str) -> None:
        s.language = lang_raw or "und"
        s.resolved_language = resolve_locale(lang_raw) if lang_raw else ""
        s.name = "" if _is_technical_stream_name(si_name) else (si_name or lang_raw)

        # ISM subtitle content is virtually always TTML/DFXP
        fc_low = (fourcc or "").lower()
        s.format = "ttml" if ("ttml" in fc_low or "dfxp" in fc_low or not fc_low) else fc_low

    def _apply_chunk_timeline(self, stream: Stream, url_template: str, bitrate: int, timeline: list[int]) -> None:
        """
        Expand a chunk timeline into ``stream.segments`` using the URL template.
        """
        url_with_bitrate = _BITRATE_RE.sub(str(bitrate), url_template)
        ref_is_simple = is_simple_relative_ref(url_with_bitrate)

        is_subtitle = stream.type == "subtitle"
        for idx, start_time in enumerate(timeline):
            url = _STARTTIME_RE.sub(str(start_time), url_with_bitrate)
            seg_url = fast_urljoin(self._base_url, url, ref_is_simple)
            seg = Segment(seg_url, idx, "media")
            
            if is_subtitle and self._timescale > 0:
                if idx + 1 < len(timeline):
                    seg.duration = (timeline[idx + 1] - start_time) / self._timescale
                else:
                    seg.duration = max(0.0, stream.duration - (start_time / self._timescale))
            stream.add_segment(seg)

    def _extract_drm(self, element) -> DRMInfo:
        """
        Extract DRM info from ``<Protection>/<ProtectionHeader>`` children.

        The ``<ProtectionHeader>`` text content is a base64-encoded PlayReady
        Object (PRO).  KID is decoded from the embedded WRMHeader XML when
        possible.
        """
        info = DRMInfo()
        protection = element.find("Protection")
        if protection is None:
            return info

        for ph in protection.findall("ProtectionHeader"):
            system_id_raw = ph.get("SystemID") or ""
            system_id = _DRMSystems.norm_system_id(system_id_raw)
            raw_data = (ph.text or "").strip()
            if not raw_data:
                continue

            if system_id == _PLAYREADY_SYSTEM_ID:
                # raw_data is a base64-encoded PRO — treat as "PSSH" for DRMInfo
                info.set_pssh(raw_data, drm_type_hint=DRMType.PLAYREADY)
                info.set_method(_PLAYREADY_SYSTEM_ID)
                logger.debug(f"ISMParser: PlayReady PRO found (len={len(raw_data)})")

                # Attempt KID extraction from the PRO binary
                try:
                    kid = _DRMSystems.extract_kid_from_playready_pro(raw_data)
                    if kid:
                        info.set_kid(kid)
                        logger.debug(f"ISMParser: KID extracted from PRO: {kid}")
                except Exception as exc:
                    logger.debug(f"ISMParser: KID extraction from PRO failed: {exc}")

            elif system_id == _WIDEVINE_SYSTEM_ID:
                info.set_pssh(raw_data, drm_type_hint=DRMType.WIDEVINE)
                info.set_method(_WIDEVINE_SYSTEM_ID)
                logger.info("ISMParser: Widevine PSSH found in ProtectionHeader")

            else:
                logger.info(f"ISMParser: unknown SystemID {system_id!r} in ProtectionHeader — skipping")

        return info

    def get_drm_info(self) -> dict:
        """
        Return DRM info in the canonical dict format used by
        ``HLSParser.get_drm_info`` and consumed by the downloader fallback::

            {
              'widevine':  [{'pssh': '...', 'type': 'Widevine',  'kid': '...'}],
              'playready': [{'pssh': '...', 'type': 'PlayReady', 'kid': '...'}],
              'fairplay':  [],
            }
        """
        if self._root is None:
            return {"widevine": [], "playready": [], "fairplay": []}
        return self._extract_drm(self._root).to_dict()
