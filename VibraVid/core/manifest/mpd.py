# 13.03.26

import logging
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from rich.console import Console

from VibraVid.core.manifest._utils import (
    calc_base_url,
    escape_bare_ampersands,
    fast_urljoin,
    fast_urljoin_auto,
    is_simple_relative_ref,
    save_raw_manifest,
)
from VibraVid.core.manifest.stream import DRMInfo, DRMType, Segment, Stream
from VibraVid.core.utils.codec import (
    AUDIO_EXTENSIONS,
    SUBTITLE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    detect_stream_type,
    get_codec_extension,
    infer_video_range,
)
from VibraVid.core.utils.language import resolve_locale
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

console = Console()
logger = logging.getLogger(__name__)

_NS = {
    "mpd": "urn:mpeg:dash:schema:mpd:2011",
    "cenc": "urn:mpeg:cenc:2013",
}
_TC_MAP = {
    "1": "SDR",
    "6": "SDR",
    "7": "SDR",
    "13": "SDR",
    "14": "SDR",
    "15": "SDR",
    "16": "PQ",
    "18": "HLG",
}
_CP_HDR_HINT = {"9"}  # BT.2020 ColourPrimaries
_FILE_EXT_WHITELIST = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | SUBTITLE_EXTENSIONS | {".mpd", ".m4s", ".cmfv", ".cmfa"}

# Ad detection
_AD_PATH_RE = re.compile(r"(?:^|/)(?:ad|ads|advert|impression|preroll|midroll|postroll)(?:/|$)", re.IGNORECASE)
_SEGMENT_TOKEN_RE = re.compile(r"\$(RepresentationID|Number|Time|Bandwidth)(?:%0?(\d+)d)?\$")
DISABLE_AD_PERIOD_DETECTION = False
_AD_MAX_DURATION_SECONDS = 31.0
_AD_HOST_DENYLIST = frozenset({
    "cf.emea-free.prd.media.max.com",
})


def _norm(v: str | None) -> str:
    return (v or "").strip().lower()

def _first_not_none(*elements):
    for el in elements:
        if el is not None:
            return el
    return None

def _stream_dedup_key(s: Stream):
    sid = _norm(getattr(s, "id", ""))
    if sid and sid != "ext" and not sid.startswith("vid:"):
        return (s.type, "id", sid)

    if s.type == "video":
        return (
            s.type,
            _norm(s.codecs),
            int(s.width or 0),
            int(s.height or 0),
            int(s.bitrate or 0),
            _norm(s.video_range),
        )

    if s.type == "audio":
        return (
            s.type,
            _norm(s.language),
            _norm(s.codecs),
            _norm(s.channels),
            int(s.sample_rate or 0),
            int(s.bitrate or 0),
            bool(s.is_sdh),
            bool(s.forced),
            bool(s.is_cc),
        )

    return (
        s.type,
        _norm(s.language),
        _norm(s.codecs),
        bool(s.forced),
        bool(s.is_cc),
        bool(s.is_sdh),
    )


def _is_compatible_stream(existing: Stream, new: Stream) -> bool:
    """Guard against a provider reusing a Representation @id across Periods for a
    genuinely different rendition. Only treat two id-matching Periods as the same logical
    track when they actually look like the same rendition; otherwise a naive
    id-only merge silently splices mismatched video resolutions or audio codecs
    into one "track", corrupting playback partway through."""
    if existing.type != new.type:
        return False
    if existing.type == "video":
        return (int(existing.width or 0), int(existing.height or 0)) == (int(new.width or 0), int(new.height or 0)) and _norm(
            existing.codecs
        ).split(".")[0] == _norm(new.codecs).split(".")[0]
    if existing.type == "audio":
        return _norm(existing.language) == _norm(new.language) and _norm(existing.codecs).split(".")[0] == _norm(new.codecs).split(".")[0]
    return True


def _rendition_key(s: Stream):
    """Identify a video/audio Representation by its rendition characteristics alone, ignoring @id."""
    if s.type == "video":
        return (s.type, _norm(s.codecs).split(".")[0], int(s.width or 0), int(s.height or 0), int(s.bitrate or 0))
    if s.type == "audio":
        return (
            s.type,
            _norm(s.language),
            _norm(s.codecs).split(".")[0],
            _norm(s.channels),
            int(s.sample_rate or 0),
            int(s.bitrate or 0),
        )
    return None


def _drm_hint_from_scheme(scheme_lower: str) -> str | None:
    result = DRMType.from_scheme(scheme_lower)
    return result if result != DRMType.UNKNOWN else None


def _video_range_from_codecs(codecs: str) -> str:
    return infer_video_range(codecs)


def _is_ad_period(
    period_url: str,
    period_element,
    has_thumbnail: bool = False,
    thumbnail_convention_active: bool = False,
    period_duration: float = 0.0,
) -> bool:
    url_is_ad = bool(_AD_PATH_RE.search(period_url or ""))
    host_is_ad = (urlparse(period_url or "").hostname or "").lower() in _AD_HOST_DENYLIST
    has_drm = period_element.find(".//mpd:ContentProtection", _NS) is not None

    xlink_actuate = ""
    for attr_name, attr_value in period_element.attrib.items():
        if attr_name.lower().endswith("actuate"):
            xlink_actuate = (attr_value or "").strip().lower()
            break

    asset_identifier = period_element.find(".//mpd:AssetIdentifier", _NS)
    asset_text = (asset_identifier.text or "").strip().lower() if asset_identifier is not None else ""
    asset_is_ad = any(token in asset_text for token in ("ad", "advert", "impression", "preroll", "midroll", "postroll"))
    xlink_is_ad = xlink_actuate == "onload"
    thumbnail_is_ad = thumbnail_convention_active and not has_thumbnail and period_duration <= _AD_MAX_DURATION_SECONDS

    logger.debug(
        f"_is_ad_period | url={period_url} | url_ad={url_is_ad} | host_ad={host_is_ad} | xlink_onload={xlink_is_ad} "
        f"| asset_ad={asset_is_ad} | has_drm={has_drm} | thumbnail_convention={thumbnail_convention_active} "
        f"| has_thumbnail={has_thumbnail} | duration={period_duration:.1f}s | thumbnail_ad={thumbnail_is_ad}"
    )

    if (url_is_ad or host_is_ad or xlink_is_ad or asset_is_ad) and not has_drm:
        return True
    return thumbnail_is_ad


def _is_file_url(url: str) -> bool:
    try:
        path = (urlparse(url).path or "").rstrip("/")
        tail = path.rsplit("/", 1)[-1]
        if not tail or "." not in tail:
            return False
        dot_idx = tail.rfind(".")
        if dot_idx <= 0:
            return False
        ext = tail[dot_idx:].lower()
        return ext in _FILE_EXT_WHITELIST
    except Exception:
        return False


class DashParser:
    def __init__(
        self,
        mpd_url: str,
        headers: dict[str, str] = None,
        provided_kid: str = None,
        content: str | None = None,
        quiet: bool = False,
    ):
        self.mpd_url = mpd_url
        self.headers = headers or {}
        self.provided_kid = provided_kid
        self.quiet = quiet
        self._injected = content
        self.raw_content: str | None = content
        self._root: ET.Element | None = None
        self._base_url = self._calc_base_url(mpd_url)
        self._mpd_query: str = urlparse(mpd_url).query if mpd_url else ""
        self._mpd_query_suffix: str = self._compute_query_suffix(self._mpd_query)
        self._uses_range_split: bool = False
        self._manifest_is_live: bool = False

    @staticmethod
    def _calc_base_url(url: str) -> str:
        return calc_base_url(url)

    # Query keys that route a request to the MANIFEST or carry MPD session context.
    _MANIFEST_ONLY_QUERY_KEYS = frozenset(
        {
            "ismaster",
            "rtype",
            "ctx",
            "ps",
            "pid",
            "reg",
        }
    )

    @classmethod
    def _compute_query_suffix(cls, source_query: str) -> str:
        """
        Resolve *source_query* (the MPD URL's own query string) into the
        exact suffix that should be appended to segment URLs lacking their
        own query"""
        if not source_query:
            return ""

        all_pairs = parse_qsl(source_query, keep_blank_values=True)
        filtered = [(k, v) for k, v in all_pairs if k.lower() not in cls._MANIFEST_ONLY_QUERY_KEYS]
        if not filtered:
            return ""

        # Preserve the original raw encoding when no key was stripped
        if len(filtered) == len(all_pairs):
            return source_query
        return urlencode(filtered)

    @staticmethod
    def _inherit_query(segment_url: str, query_suffix: str) -> str:
        """Append the precomputed *query_suffix* (see ``_compute_query_suffix``)"""
        if not query_suffix:
            return segment_url
        if "?" in segment_url:
            return segment_url
        return f"{segment_url}?{query_suffix}"

    def fetch_manifest(self) -> bool:
        start_parsing_time = time.time()

        if self._injected:
            self.raw_content = self._injected
            try:
                self._root = ET.fromstring(escape_bare_ampersands(self.raw_content))
                self._resolve_base_url()
                logger.info(f"DashParser: injected XML parsed in {time.time() - start_parsing_time:.2f}s")
                return True
            except ET.ParseError as exc:
                logger.error(f"DashParser: injected XML parse error: {exc}")
                self._injected = None

        if self.mpd_url.startswith("file://"):
            try:
                from urllib.request import url2pathname

                local_path = Path(url2pathname(urlparse(self.mpd_url).path))
                self.raw_content = local_path.read_text(encoding="utf-8")
                self._base_url = local_path.parent.as_uri() + "/"
                self._root = ET.fromstring(escape_bare_ampersands(self.raw_content))
                self._resolve_base_url()
                logger.info(f"DashParser: local file read and parsed successfully in {time.time() - start_parsing_time:.2f}s")
                return True
            except Exception as exc:
                console.print(f"[red]Failed to read local DASH manifest: {exc}.")
                logger.error(f"DashParser: local file read failed: {exc}")
                return False

        try:
            timeout = config_manager.config.get_int("REQUESTS", "timeout")
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
                r = c.get(self.mpd_url)
                r.raise_for_status()
                self.raw_content = r.text
                effective_url = str(r.url)

            # The manifest host may 302 to a session/edge-specific CDN node --
            # resolve against that final host before an explicit <BaseURL> (if
            # any) is applied on top of it in _resolve_base_url().
            if effective_url and effective_url != self.mpd_url:
                self._base_url = self._calc_base_url(effective_url)

            self._root = ET.fromstring(escape_bare_ampersands(self.raw_content))
            self._resolve_base_url()
            logger.info(f"DashParser: fetched and parsed MPD in {time.time() - start_parsing_time:.2f}s")
            return True
        except Exception as exc:
            console.print(f"[red]Error fetching/parsing MPD manifest: {exc}[/red]")
            logger.error(f"DashParser: fetch/parse failed: {exc}")
            return False

    @property
    def live_meta(self) -> dict:
        """Return live-scheduling metadata for dynamic MPDs."""
        if self._root is None:
            return {"min_update_period": 4.0, "is_ended": True, "availability_start_time": ""}

        mpd_type = (self._root.get("type") or "dynamic").strip().lower()
        raw_period = self._root.get("minimumUpdatePeriod", "")
        upd = self._parse_iso_duration(raw_period)

        return {
            "min_update_period": upd if upd > 0 else 4.0,
            "is_ended": mpd_type == "static",
            "availability_start_time": self._root.get("availabilityStartTime", ""),
        }

    def save_raw(self, directory: Path) -> Path:
        return save_raw_manifest(self.raw_content, directory, "raw.mpd")

    def parse_streams(self) -> list[Stream]:
        if self._root is None:
            return []

        # Determine if MPD is dynamic (live)
        mpd_type = (self._root.get("type") or "").strip().lower()
        manifest_is_live = mpd_type == "dynamic"
        self._manifest_is_live = manifest_is_live

        streams: list[Stream] = []
        dur_str = self._root.get("mediaPresentationDuration", "")
        global_duration = self._parse_iso_duration(dur_str)

        all_periods = self._root.findall("mpd:Period", _NS)
        global_seen_keys: set = set()
        period_durations: dict[int, float] = {}
        duplicate_source_periods: set = set()

        # Reset range-split tracking
        self._uses_range_split = False

        def _period_has_thumbnail(period_element) -> bool:
            return any(
                "image" in (a.get("contentType") or a.get("mimeType") or "").lower()
                for a in period_element.findall("mpd:AdaptationSet", _NS)
            )

        thumbnail_convention_active = any(_period_has_thumbnail(p) for p in all_periods)

        for period_idx, period in enumerate(all_periods):
            period_base_url = self._resolve_element_base_url(period, self._base_url)
            period_duration = self._parse_iso_duration(period.get("duration", dur_str))
            if not DISABLE_AD_PERIOD_DETECTION and _is_ad_period(
                period_base_url,
                period,
                has_thumbnail=_period_has_thumbnail(period),
                thumbnail_convention_active=thumbnail_convention_active,
                period_duration=period_duration,
            ):
                logger.info(f"DASH period skipped (advertisement) | period={period_idx} url={period_base_url}")
                continue

            period_durations[period_idx] = period_duration
            period_start = self._parse_iso_duration(period.get("start", ""))
            period_seen_keys: set = set()

            for _adapt_idx, adapt in enumerate(period.findall("mpd:AdaptationSet", _NS)):
                adapt_base_url = self._resolve_element_base_url(adapt, period_base_url)
                mime = (adapt.get("contentType") or adapt.get("mimeType") or "").lower()

                if "video" in mime:
                    stype = "video"
                elif "audio" in mime:
                    stype = "audio"
                elif "text" in mime or "subtitle" in mime:
                    stype = "subtitle"
                elif "image" in mime:
                    continue
                else:
                    first_rep = adapt.find(".//mpd:Representation", _NS)
                    codecs_hint = (
                        (first_rep.get("codecs") or adapt.get("codecs") or "").lower() if first_rep is not None else ""
                    )
                    stype = detect_stream_type(codecs_hint)
                    if not stype:
                        logger.info(f"DashParser: AdaptationSet skipped — unknown mime={mime!r} codecs={codecs_hint!r}")
                        continue

                adapt_drm = self._extract_drm(adapt)

                for rep in adapt.findall("mpd:Representation", _NS):
                    rep_id = rep.get("id", "")
                    rep_base_url = self._resolve_element_base_url(rep, adapt_base_url)
                    if "/ad/" in rep_base_url.lower() and not adapt_drm.is_encrypted():
                        logger.info(f"DASH stream skipped (advertisement) | id={rep_id!r} period={period_idx} url={rep_base_url}")
                        continue

                    s = self._parse_representation(
                        rep, 
                        adapt, 
                        stype, 
                        adapt_drm, 
                        global_duration or period_duration, 
                        period_start, 
                        adapt_base_url,
                        period_duration=period_duration,
                    )
                    if s is None:
                        continue

                    # Tag every segment with its Period so the downloader can process
                    # multi-period tracks (distinct source files / mixed clear+encrypted
                    # per Period) one Period at a time instead of blindly concatenating.
                    period_encrypted = bool(s.drm and s.drm.is_encrypted())
                    for seg in s.segments:
                        seg.period_idx = period_idx
                        seg.encrypted = period_encrypted

                    dedup_key = _stream_dedup_key(s)

                    if dedup_key in period_seen_keys:
                        # Duplicate representation ID within the same Period (different AdaptationSet)
                        logger.debug(f"DASH stream skipped (intra-period duplicate) | id={rep_id!r} period={period_idx}")
                        continue

                    period_seen_keys.add(dedup_key)

                    matched_stream = None
                    if dedup_key in global_seen_keys:
                        for existing_stream in streams:
                            if _stream_dedup_key(existing_stream) == dedup_key and _is_compatible_stream(existing_stream, s):
                                matched_stream = existing_stream
                                break

                    if matched_stream is None and stype in ("video", "audio"):
                        rendition_key = _rendition_key(s)
                        if rendition_key is not None:
                            for existing_stream in streams:
                                if existing_stream is s or existing_stream.type != stype:
                                    continue

                                if _rendition_key(existing_stream) != rendition_key:
                                    continue
                                
                                # Only bridge a Period boundary this way, not two
                                # different Representations that happen to share a
                                # rendition within the same Period.
                                existing_max_period = max(
                                    (sg.period_idx for sg in existing_stream.segments), default=-1
                                )
                                if existing_max_period < period_idx:
                                    matched_stream = existing_stream
                                    break

                    if matched_stream is not None:
                        # Some SSAI/CDN manifests hand a distinct URL per Period to what
                        # is, on the wire, the exact same media segment (a provider-side
                        # bug -- seen e.g. with SegmentBase byte-range representations
                        # reused verbatim across several Period entries). Downloading and
                        # decrypting that content again per Period would just duplicate
                        # it N times and desync the track. Only drop a Period whole: if
                        # every one of its media segments already showed up in an earlier
                        # Period it's a full duplicate; dropping individual segments would
                        # leave the survivors with gaps and produce a broken merged file.
                        new_media = [sg for sg in s.segments if sg.seg_type == "media"]
                        existing_media_urls = {sg.url for sg in matched_stream.segments if sg.seg_type == "media"}
                        is_full_duplicate = bool(new_media) and all(sg.url in existing_media_urls for sg in new_media)

                        if is_full_duplicate:
                            new_urls = {sg.url for sg in new_media}
                            source_period_idx = next(
                                (
                                    sg.period_idx
                                    for sg in matched_stream.segments
                                    if sg.seg_type == "media" and sg.url in new_urls
                                ),
                                None,
                            )
                            if source_period_idx is not None:
                                duplicate_source_periods.add(source_period_idx)

                            logger.debug(
                                f"DASH stream Period {period_idx} is a full duplicate of an earlier Period "
                                f"(same {len(new_media)} media URL(s)) -- skipped | id={rep_id!r}"
                            )
                        else:
                            logger.debug(f"DASH stream extended (multi-period) | id={rep_id!r} period={period_idx} lang={s.language} bw={s.bitrate}")
                            matched_stream.segments.extend(s.segments)

                        # A later Period may be encrypted while the first (e.g. a
                        # clear intro) was not: adopt the encrypted DRM so keys are
                        # fetched and the encrypted Period actually gets decrypted.
                        # Also handle key rotation: merge KIDs/PSSHs when both
                        # Periods carry DRM but with different keys.
                        if period_encrypted:
                            if not matched_stream.drm.is_encrypted():
                                matched_stream.drm = s.drm
                            else:
                                for kid in s.drm.get_all_kids():
                                    matched_stream.drm.set_kid(kid)

                                for dtype in s.drm.get_all_drm_types():
                                    pssh = s.drm.get_pssh_for(dtype)
                                    if pssh:
                                        matched_stream.drm.set_pssh(pssh, drm_type_hint=dtype)

                                if s.drm.method and not matched_stream.drm.method:
                                    matched_stream.drm.method = s.drm.method

                        continue

                    if dedup_key in global_seen_keys:
                        # id collides with an already-seen stream's dedup key, but the
                        # actual rendition (resolution/codec for video, codec for audio)
                        # doesn't match -- the provider reused the Representation @id for
                        # a genuinely different rendition in this Period (seen with SSAI ad
                        # Periods reusing the content ladder's ids for unrelated
                        # resolutions/codecs). Register it as its own track instead of
                        # corrupting the existing one.
                        logger.debug(
                            f"DASH stream id={rep_id!r} period={period_idx} reuses an id already seen for a "
                            f"different rendition (res/codec mismatch) -- treating as a separate stream"
                        )

                    global_seen_keys.add(dedup_key)
                    streams.append(s)
                    if not self.quiet:
                        logger.info(f"{s}")

        # A Period that turns out to be the source of a byte-identical duplicate
        # elsewhere in the manifest (already detected above) is itself almost
        # always an SSAI ad -- the same creative just gets stitched in more than
        # once. _is_ad_period() can't catch these when the manifest carries none
        # of its usual signals (no "ad" in the URL, no xlink, no AssetIdentifier),
        # so drop the surviving first instance too when it's short enough to be a
        # spot rather than legitimate repeated content.
        ad_period_idxs = {
            p for p in duplicate_source_periods if 0 < period_durations.get(p, 0.0) <= _AD_MAX_DURATION_SECONDS
        }
        if ad_period_idxs:
            logger.info(f"DASH periods identified as advertisement (source of a duplicated Period elsewhere in the manifest): {sorted(ad_period_idxs)}")
            kept_streams: list[Stream] = []
            for st in streams:
                before = len(st.segments)
                st.segments = [sg for sg in st.segments if sg.period_idx not in ad_period_idxs]
                if len(st.segments) != before:
                    logger.debug(f"DASH stream {st.id!r}: dropped {before - len(st.segments)} segment(s) from ad period(s)")
                if st.segments:
                    kept_streams.append(st)
                else:
                    logger.debug(f"DASH stream dropped entirely (only had ad-period segments) | id={st.id!r} type={st.type}")
            streams = kept_streams

        if manifest_is_live:
            for s in streams:
                s.is_live = True

        if not self.quiet:
            logger.info(f"DASH manifest type: {'LIVE' if manifest_is_live else 'VOD'}")
        if self._uses_range_split:
            logger.info("DASH manifest uses range-split (SegmentBase with byte ranges). Live decryption is disabled for all streams in this manifest.")

        return streams

    def _parse_representation(self, rep, adapt, stype, adapt_drm, global_dur, period_start, base_url, period_duration=0.0):
        rep_id = rep.get("id", "")
        bandwidth = int(rep.get("bandwidth", 0))
        avg_bw = int(rep.get("averageBandwidth", 0)) or int(adapt.get("averageBandwidth", 0))
        rep_base_url = self._resolve_element_base_url(rep, base_url)

        s = Stream(type=stype, id=rep_id, format="dash")
        s.bitrate = bandwidth
        s.avg_bitrate = avg_bw
        s.duration = global_dur

        role_el = adapt.find(".//mpd:Role", _NS)
        if role_el is not None:
            s.role = role_el.get("value", "main")

        label_el = _first_not_none(rep.find("mpd:Label", _NS), adapt.find("mpd:Label", _NS))
        if label_el is not None and label_el.text:
            s.label = label_el.text.strip()

        if stype == "video":
            self._parse_video_fields(rep, adapt, s)
        elif stype == "audio":
            self._parse_audio_fields(rep, adapt, s)
        elif stype == "subtitle":
            self._parse_subtitle_fields(rep, adapt, s)

        # DRM: rep overrides adapt; merge when both present.
        rep_has_cp = rep.find(".//mpd:ContentProtection", _NS) is not None
        rep_drm = self._extract_drm(rep) if rep_has_cp else DRMInfo()
        if rep_drm.is_encrypted() and adapt_drm.is_encrypted():
            for dtype, pssh in adapt_drm._pssh_by_type.items():
                if dtype not in rep_drm._pssh_by_type:
                    rep_drm.set_pssh(pssh, drm_type_hint=dtype)

            for kid in adapt_drm.get_all_kids():
                rep_drm.set_kid(kid)
            s.drm = rep_drm

        elif rep_drm.is_encrypted():
            s.drm = rep_drm

        elif adapt_drm.is_encrypted():
            s.drm = adapt_drm
        else:
            s.drm = DRMInfo()

        if s.drm.is_encrypted() and not s.drm.method:
            # WebM CENC has no cbcs/cbc1/cens equivalent — only AES-CTR full/partitioned-sample
            mime_hint = (rep.get("mimeType") or adapt.get("mimeType") or "").lower()
            if "webm" in mime_hint:
                s.drm.method = "cenc"

        # Segments: SegmentTemplate > SegmentList > BaseURL single-file
        tmpl = _first_not_none(rep.find("mpd:SegmentTemplate", _NS), adapt.find("mpd:SegmentTemplate", _NS))
        seg_list = _first_not_none(rep.find("mpd:SegmentList", _NS), adapt.find("mpd:SegmentList", _NS))
        seg_base = _first_not_none(rep.find("mpd:SegmentBase", _NS), adapt.find("mpd:SegmentBase", _NS))

        if tmpl is not None:
            self._apply_segment_template(tmpl, rep_id, s, period_start, rep_base_url, period_duration=period_duration)
            s.supports_live_decryption = True  # True segments available
        elif seg_list is not None:
            self._apply_segment_list(seg_list, s, rep_base_url)
            s.supports_live_decryption = True  # True segments available
        elif seg_base is not None:
            # SegmentBase: may be range-split (byte ranges) or simple single-file
            # Live decryption NOT suitable for range-split files (no true segments)
            s.supports_live_decryption = False
            self._uses_range_split = True

            index_range = seg_base.get("indexRange", "")
            media_range = seg_base.find("mpd:Initialization", _NS)
            media_range = media_range.get("range", "") if media_range is not None else ""

            if index_range or media_range:
                logger.debug(f"DASH range-split detected for stream {rep_id!r}: indexRange={index_range!r}, mediaRange={media_range!r}. Live decryption disabled.")

            # An indexRange means the single file starts with a real sidx box, so its
            # true segment boundaries are known. Splitting it on fixed byte chunks
            # instead cuts through moof/mdat pairs: the reassembled file loses its
            # encryption boxes and the decryptor then reports "no encryption indicators"
            # for every track. Ask the downloader for the sidx-based split instead.
            if index_range:
                s.no_range_split = True

            # Get the media URL
            rep_base = rep.find("mpd:BaseURL", _NS)
            if rep_base is not None and rep_base.text:
                rep_rel = rep_base.text.strip()
                if rep_base_url.rstrip("/").endswith(rep_rel):
                    media_url = rep_base_url.rstrip("/")
                else:
                    media_url = urljoin(rep_base_url, rep_rel)
            else:
                media_url = rep_base_url.rstrip("/")

            # A SegmentBase file opens with the init segment (ftyp+moov, which carries the
            # tenc/senc setup the decryptor looks for). Without it the reassembled file is
            # a bare moof/mdat sequence, so decryption reports "no encryption indicators".
            next_segment_number = 0
            if media_range:
                s.add_segment(Segment(media_url, next_segment_number, "init", byte_range=media_range))
                next_segment_number += 1

            # The media part is emitted as one single-file segment; the downloader splits
            # it on the sidx boundaries.
            s.add_segment(Segment(media_url, next_segment_number, "media"))
        else:
            # No segmentation info - single file
            rep_base = rep.find("mpd:BaseURL", _NS)
            if rep_base is not None and rep_base.text:
                rep_rel = rep_base.text.strip()
                if rep_base_url.rstrip("/").endswith(rep_rel):
                    media_url = rep_base_url.rstrip("/")
                else:
                    media_url = urljoin(rep_base_url, rep_rel)
                s.add_segment(Segment(media_url, 0, "media"))
            else:
                s.add_segment(Segment(rep_base_url.rstrip("/"), 0, "media"))

            # Single file without ranges might still work with live decryption if we split it
            # But safer to mark as False unless we know it's segmented
            s.supports_live_decryption = False

        return s

    def _parse_video_fields(self, rep, adapt, s):
        s.width = int(rep.get("width") or adapt.get("width") or 0)
        s.height = int(rep.get("height") or adapt.get("height") or 0)
        s.resolution = f"{s.width}x{s.height}" if s.width and s.height else ""
        s.fps = rep.get("frameRate", adapt.get("frameRate", ""))
        s.codecs = rep.get("codecs") or adapt.get("codecs", "")
        s.scan_type = (rep.get("scanType") or adapt.get("scanType") or "").lower()
        s.video_range = (
            self._extract_video_range(rep) or self._extract_video_range(adapt) or _video_range_from_codecs(s.codecs)
        )

    def _parse_audio_fields(self, rep, adapt, s):
        raw_lang = adapt.get("lang", "und")
        s.language = raw_lang
        s.resolved_language = resolve_locale(raw_lang)
        s.codecs = rep.get("codecs") or adapt.get("codecs", "")

        for acc_el in adapt.findall(".//mpd:AudioChannelConfiguration", _NS):
            val = acc_el.get("value", "")
            if val:
                s.channels = val
                break
        if not s.channels:
            s.channels = rep.get("audioChannelConfiguration", adapt.get("audioChannelConfiguration", ""))

        # Sample rate: element first, then attribute (space-sep → take last/highest)
        sr_el = _first_not_none(rep.find("mpd:AudioSamplingRate", _NS), adapt.find("mpd:AudioSamplingRate", _NS))
        if sr_el is not None and sr_el.text:
            try:
                s.sample_rate = int(sr_el.text.strip().split()[0])
            except ValueError:
                pass
        if not s.sample_rate:
            sr_attr = rep.get("audioSamplingRate") or adapt.get("audioSamplingRate")
            if sr_attr:
                try:
                    s.sample_rate = int(sr_attr.strip().split()[-1])
                except ValueError:
                    pass

        s.is_sdh = self._is_accessibility_sdh(rep) or self._is_accessibility_sdh(adapt)

    def _parse_subtitle_fields(self, rep, adapt, s):
        raw_lang = adapt.get("lang", "und")
        s.language = raw_lang
        s.resolved_language = resolve_locale(raw_lang)
        s.codecs = rep.get("codecs") or adapt.get("codecs", "")

        # ── Detect subtitle container format from mimeType ────────────────────
        # Priority: Representation mimeType > AdaptationSet mimeType > codecs
        mime_raw = (rep.get("mimeType") or adapt.get("mimeType") or adapt.get("contentType") or "").lower().strip()

        # Map known MIME types / codec strings to a clean format token
        _MIME_TO_FMT = {
            "text/vtt": "vtt",
            "text/webvtt": "vtt",
            "application/ttml+xml": "ttml",
            "text/ttml": "ttml",
            "application/mp4": None,
            "video/mp4": None,
        }

        fmt = _MIME_TO_FMT.get(mime_raw)
        if fmt is None:
            codec_lc = (s.codecs or "").lower().strip()
            fmt = get_codec_extension(codec_lc, default="vtt")

        # Pure text/vtt streams that carry ".vtt" segments but no codec string
        if not fmt and "vtt" in mime_raw:
            fmt = "vtt"
        if not fmt and "ttml" in mime_raw:
            fmt = "ttml"

        # Default for unknown DASH text streams
        if not fmt:
            fmt = "vtt"

        # wvtt is a special case: WebVTT packaged inside fMP4
        if fmt == "wvtt" or (s.codecs or "").lower().strip() == "wvtt":
            s.is_wvtt_mp4 = True
            fmt = "wvtt"
            logger.debug(f"DashParser: subtitle id={s.id!r} lang={raw_lang!r} flagged as wvtt-mp4")

        s.format = fmt

        # ── Roles / flags ─────────────────────────────────────────────────────
        role_values = {el.get("value", "").lower() for el in adapt.findall(".//mpd:Role", _NS)}
        if "forced-subtitle" in role_values or "forced_subtitle" in role_values:
            s.forced = True

        if "caption" in role_values or "captions" in role_values:
            s.is_cc = True

        s.is_sdh = self._is_accessibility_sdh(adapt)

        if s.forced and not s.name:
            s.name = f"{s.language} [Forced]"
        elif s.is_cc and not s.name:
            s.name = f"{s.language} [CC]"

    def _extract_video_range(self, element) -> str:
        tc_value = None
        cp_value = None
        for tag in ("mpd:SupplementalProperty", "mpd:EssentialProperty"):
            for prop in element.findall(f".//{tag}", _NS):
                scheme = (prop.get("schemeIdUri") or "").lower()
                value = (prop.get("value") or "").strip()
                if "dolbyvision" in scheme or "dolby" in scheme:
                    return "DV"

                if "hdr10-plus" in scheme or "hdr10plus" in scheme:
                    return "HDR10+"

                val_up = value.upper()
                if val_up in ("HDR10", "HDR10+", "HLG", "PQ", "HDR", "DV"):
                    return val_up

                if "transfercharacteristics" in scheme:
                    tc_value = value

                if "colourprimaries" in scheme or "colorprimaries" in scheme:
                    cp_value = value

        if tc_value and tc_value in _TC_MAP:
            vr = _TC_MAP[tc_value]
            return vr if vr != "SDR" else ""
        if cp_value in _CP_HDR_HINT:
            return "HDR10"

        return ""

    def _is_accessibility_sdh(self, element) -> bool:
        for acc in element.findall(".//mpd:Accessibility", _NS):
            scheme = (acc.get("schemeIdUri") or "").lower()
            value = (acc.get("value") or "").lower().strip()
            if "audiopurpose" in scheme and value == "1":
                return True
            if "role" in scheme and value in ("caption", "description"):
                return True
            if value in {"1", "2", "hearing-impaired", "description", "caption"}:
                return True
        return False

    def _extract_drm(self, element) -> DRMInfo:
        info = DRMInfo()
        for cp in element.findall(".//mpd:ContentProtection", _NS):
            scheme = (cp.get("schemeIdUri") or "").lower()
            info.set_method(scheme)

            # Also check the `value` attribute for encryption mode (e.g. value="cbcs")
            # when schemeIdUri is the generic mp4protection URI.
            cp_value = (cp.get("value") or "").lower()
            if cp_value and cp_value in ("cbcs", "cbc1", "cens", "cenc"):
                info.method = cp_value

            drm_hint = _drm_hint_from_scheme(scheme)

            # Try to find PSSH in multiple locations:
            # 1. cenc:pssh
            pssh_el = cp.find(".//cenc:pssh", _NS)

            # 2. mpd:pssh
            if pssh_el is None:
                pssh_el = cp.find("mpd:pssh", _NS)

            # 3. direct child pssh without namespace
            if pssh_el is None:
                pssh_el = cp.find("pssh")

            if pssh_el is not None and pssh_el.text:
                info.set_pssh(pssh_el.text.strip(), drm_type_hint=drm_hint)

            # PlayReady specific: check for <pro> element
            _MSPR_NS = "urn:microsoft:playready"
            pro_el = cp.find(f"{{{_MSPR_NS}}}pro")
            if pro_el is not None and pro_el.text and pro_el.text.strip():
                if not info.get_pssh_for(DRMType.PLAYREADY):
                    info.set_pssh(pro_el.text.strip(), drm_type_hint=DRMType.PLAYREADY)
                    logger.info("DashParser: PlayReady PSSH extracted from <pro> element")

            # Extract KID from multiple possible attribute names
            for attr in ("{urn:mpeg:cenc:2013}default_KID", "cenc:default_KID", "default_KID"):
                kid = cp.get(attr)
                if kid:
                    info.set_kid(kid)
                    info.default_kid = info.kid
                    break

        if not info.drm_type and (info.kid or info.default_kid):
            for cp in element.findall(".//mpd:ContentProtection", _NS):
                scheme = (cp.get("schemeIdUri") or "").lower()
                dtype = _drm_hint_from_scheme(scheme)
                logger.debug(f"DashParser: inferring DRM type from scheme | scheme={scheme} | inferred_type={dtype}")

                if dtype:
                    if dtype not in info._drm_types:
                        info._drm_types.append(dtype)
                    if not info.drm_type:
                        info.drm_type = dtype

        if not info.kid and self.provided_kid:
            info.set_kid(self.provided_kid)
            info.default_kid = info.kid
        return info

    def _resolve_base_url(self) -> None:
        if self._root is None:
            return

        base_el = self._root.find("mpd:BaseURL", _NS)
        if base_el is not None and base_el.text and base_el.text.strip():
            candidate = base_el.text.strip()
            if candidate.startswith(("http://", "https://", "//")):
                self._base_url = candidate if candidate.endswith("/") else candidate + "/"
            else:
                self._base_url = urljoin(self._base_url, candidate)
                if not self._base_url.endswith("/"):
                    self._base_url += "/"

    @staticmethod
    def _resolve_element_base_url(element, parent_base: str) -> str:
        base_el = element.find("mpd:BaseURL", _NS)
        if base_el is not None and base_el.text and base_el.text.strip():
            resolved = urljoin(parent_base, base_el.text.strip())
            if resolved.endswith("/") or not _is_file_url(resolved):
                return resolved if resolved.endswith("/") else resolved + "/"

            return resolved
        return parent_base

    @staticmethod
    def _expand_segment_template_tokens(
        template: str, rep_id: str, bandwidth: int, number: int | None = None, time_value: int | None = None
    ) -> str:
        if not template:
            return ""

        # DASH escaping for literal '$'
        escaped = template.replace("$$", "__DASH_DOLLAR__")

        def _repl(match: re.Match) -> str:
            token = match.group(1)
            width_raw = match.group(2)
            width = int(width_raw) if width_raw else 0

            if token == "RepresentationID":
                value = rep_id

            elif token == "Bandwidth":
                value = str(int(bandwidth or 0))

            elif token == "Number":
                if number is None:
                    return match.group(0)
                value = str(int(number))

            elif token == "Time":
                if time_value is None:
                    return match.group(0)
                value = str(int(time_value))

            else:
                return match.group(0)

            if width and value.isdigit():
                return value.zfill(width)
            return value

        expanded = _SEGMENT_TOKEN_RE.sub(_repl, escaped)
        return expanded.replace("__DASH_DOLLAR__", "$")

    def _apply_segment_template(self, tmpl, rep_id, stream, period_start, base_url, period_duration=0.0):
        if "/ad/" in base_url.lower():
            logger.info(f"DashParser: segment skipped (ad path) | url={base_url}")
            return

        init_tpl = tmpl.get("initialization", "")
        media_tpl = tmpl.get("media", "")
        start_num = int(tmpl.get("startNumber", 1))
        timescale = int(tmpl.get("timescale", 1))
        bandwidth = int(stream.bitrate or 0)
        start_time = int(period_start * timescale) if period_start else 0

        if init_tpl:
            init_url = self._expand_segment_template_tokens(
                init_tpl,
                rep_id,
                bandwidth,
                number=start_num,
                time_value=start_time,
            )
            stream.add_segment(
                Segment(self._inherit_query(urljoin(base_url, init_url), self._mpd_query_suffix), 0, "init")
            )

        timeline = tmpl.find(".//mpd:SegmentTimeline", _NS)
        if timeline is not None:
            seg_num = start_num
            current_time = start_time
            ref_is_simple = is_simple_relative_ref(media_tpl)

            for s_el in timeline.findall("mpd:S", _NS):
                t = s_el.get("t")

                if t is not None:
                    current_time = int(t)

                d = int(s_el.get("d", 0))
                r = int(s_el.get("r", 0))

                seg_dur_s = d / timescale if timescale > 0 else 0.0
                for _ in range(r + 1):
                    seg_url = self._expand_segment_template_tokens(
                        media_tpl,
                        rep_id,
                        bandwidth,
                        number=seg_num,
                        time_value=current_time,
                    )

                    stream.add_segment(
                        Segment(
                            self._inherit_query(fast_urljoin(base_url, seg_url, ref_is_simple), self._mpd_query_suffix),
                            seg_num,
                            "media",
                            duration=seg_dur_s,
                        )
                    )
                    current_time += d
                    seg_num += 1

        elif "$Number" in media_tpl:
            seg_duration = int(tmpl.get("duration", 0))
            if seg_duration <= 0:
                logger.error("DashParser: SegmentTemplate $Number$ without timeline: missing duration — cannot generate segments")
                return

            if stream.duration <= 0 and self._manifest_is_live:
                live_window = self._estimate_live_number_window(seg_duration, timescale, start_num)
                if not live_window:
                    logger.error("DashParser: live $Number$ template could not be windowed")
                    return
                first_num, last_num = live_window
                number_iter = range(first_num, last_num + 1)
            else:
                # Use this Period's own duration for the segment count, not the
                # Stream's (which may hold the full multi-period presentation
                # duration) -- each Period's SegmentTemplate starts numbering
                # from startNumber again, so sizing off the total duration
                # requests segments past what that Period actually has on the CDN.
                effective_duration = period_duration or stream.duration
                total_segments = math.ceil(effective_duration * timescale / seg_duration)
                number_iter = range(start_num, start_num + total_segments)

            seg_dur_s = seg_duration / timescale if timescale > 0 else 0.0
            ref_is_simple = is_simple_relative_ref(media_tpl)
            for i in number_iter:
                seg_url = self._expand_segment_template_tokens(media_tpl, rep_id, bandwidth, number=i)
                stream.add_segment(
                    Segment(
                        self._inherit_query(fast_urljoin(base_url, seg_url, ref_is_simple), self._mpd_query_suffix),
                        i,
                        "media",
                        duration=seg_dur_s,
                    )
                )

        elif "$Time" in media_tpl:
            logger.error("DashParser: SegmentTemplate $Time$ without SegmentTimeline — skipping")

    def _estimate_live_number_window(
        self, seg_duration: int, timescale: int, start_num: int
    ) -> tuple[int, int] | None:
        """Estimate a rolling window of $Number$ segments for dynamic MPDs without SegmentTimeline."""
        if self._root is None:
            return None

        ast = self._root.get("availabilityStartTime", "")
        if not ast:
            return None

        try:
            ast_dt = datetime.fromisoformat(ast.replace("Z", "+00:00"))
            if ast_dt.tzinfo is None:
                ast_dt = ast_dt.replace(tzinfo=timezone.utc)

            now_dt = datetime.now(timezone.utc)
            elapsed = max(0.0, (now_dt - ast_dt).total_seconds())
            segment_seconds = float(seg_duration) / float(timescale or 1)
            if segment_seconds <= 0:
                return None

            current_number = int(elapsed // segment_seconds)
            buffer_depth = self._parse_iso_duration(self._root.get("timeShiftBufferDepth", ""))
            window_count = max(3, int(math.ceil(buffer_depth / segment_seconds)) if buffer_depth > 0 else 3)
            first_num = max(start_num, current_number - window_count + 1)
            return first_num, max(first_num, current_number)

        except Exception as exc:
            logger.error(f"DashParser: live window estimation failed: {exc}")
            return None

    def _apply_segment_list(self, seg_list, stream, base_url):
        seg_urls = seg_list.findall("mpd:SegmentURL", _NS)

        # For byte-range (single-file) SegmentLists, find where the first media range
        # starts so we can close any gap between the Initialization range and it.
        first_media_start = None
        for seg_el in seg_urls:
            if not seg_el.get("media"):
                mr = seg_el.get("mediaRange", "")
                if "-" in mr and mr.split("-")[0].isdigit():
                    first_media_start = int(mr.split("-")[0])
                    break

        init_el = seg_list.find("mpd:Initialization", _NS)
        if init_el is not None:
            src = init_el.get("sourceURL", "")
            if src:
                # A sourceURL may still carry a byte range:
                # honour it, otherwise the whole multi-rep init file is fetched and
                # the wrong (first/lowest) moov ends up describing the track.
                init_range = init_el.get("range", "")
                stream.add_segment(
                    Segment(
                        self._inherit_query(urljoin(base_url, src), self._mpd_query_suffix),
                        0,
                        "init",
                        byte_range=init_range,
                    )
                )
            else:
                init_range = init_el.get("range", "")
                if init_range:
                    # Close the gap between the init range and the first media range. That
                    # gap is usually a `sidx` index box the SegmentList doesn't list
                    parts = init_range.split("-")
                    if (
                        len(parts) == 2
                        and parts[1].isdigit()
                        and first_media_start is not None
                        and int(parts[1]) + 1 < first_media_start
                    ):
                        init_range = f"{parts[0]}-{first_media_start - 1}"
                    stream.add_segment(
                        Segment(
                            self._inherit_query(base_url.rstrip("/"), self._mpd_query_suffix),
                            0,
                            "init",
                            byte_range=init_range,
                        )
                    )

        for idx, seg_el in enumerate(seg_urls, start=1):
            media_url = seg_el.get("media", "")
            media_range = seg_el.get("mediaRange", "")
            if media_url:
                # A SegmentURL can legally carry both attributes at once: the
                # same "media" URL repeated across many SegmentURL entries
                # (often with the same query string on every one), each
                # naming its own byte-range slice via mediaRange. Dropping
                # mediaRange here just because "media" is also present loses
                # the per-segment range entirely, which downstream treats as
                # one giant single-file resource -- the whole thing gets
                # fetched instead of each small ranged slice.
                stream.add_segment(
                    Segment(
                        self._inherit_query(fast_urljoin_auto(base_url, media_url), self._mpd_query_suffix),
                        idx,
                        "media",
                        byte_range=media_range,
                    )
                )
            elif media_range:
                stream.add_segment(
                    Segment(
                        self._inherit_query(base_url.rstrip("/"), self._mpd_query_suffix),
                        idx,
                        "media",
                        byte_range=media_range,
                    )
                )

    @staticmethod
    def _parse_iso_duration(s: str) -> float:
        if not s:
            return 0.0
        try:
            m = re.match(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?", s)
            if m:
                years = int(m.group(1) or 0)
                months = int(m.group(2) or 0)
                days = int(m.group(3) or 0)
                hours = int(m.group(4) or 0)
                minutes = int(m.group(5) or 0)
                seconds = float(m.group(6) or 0)
                return years * 31536000 + months * 2592000 + days * 86400 + hours * 3600 + minutes * 60 + seconds
        except Exception:
            pass
        return 0.0