# 13.03.26

import base64
import binascii
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from rich.console import Console

from VibraVid.core.drm.system import _DRMSystems
from VibraVid.core.manifest._utils import calc_base_url, save_raw_manifest
from VibraVid.core.manifest.stream import DRMInfo, DRMType, Stream
from VibraVid.core.utils.codec import VIDEO_CODEC_PREFIXES, infer_video_range
from VibraVid.core.utils.language import resolve_locale
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)
console = Console()

_CC_NAME_RE = re.compile(r"\[CC\]|\bCC\b|closed[- _]captions?|\bSDH\b", re.IGNORECASE)
_SDH_NAME_RE = re.compile(r"\[SDH\]|\bSDH\b|hearing[- _]impaired|\bHI\b", re.IGNORECASE)
_FORCED_NAME_RE = re.compile(r"\[forced\]|\bforced\b", re.IGNORECASE)
_COMPOUND_LANG_RE = re.compile(r"^(.+?)[-_](forced|cc|sdh|hi|default)$", re.IGNORECASE)


def _request_timeout() -> int:
    return config_manager.config.get_int("REQUESTS", "timeout")


def _make_video_id(s: Stream) -> str:
    """Build a stable synthetic ID for a video variant.
    Priority: STABLE-VARIANT-ID (already in s.id) → vid:{res}@{bw}"""
    if s.id and not s.id.startswith("vid:"):
        return s.id
    res = f"{s.width}x{s.height}" if s.width and s.height else (s.resolution or "?x?")
    return f"vid:{res}@{s.bitrate}"


def _make_rendition_id(group_id: str, language: str, name: str) -> str:
    """Build a stable synthetic ID for an audio/subtitle rendition.
    Priority: STABLE-RENDITION-ID (caller) → {group_id}:{language}"""
    parts = [p for p in (group_id, language or name) if p]
    return ":".join(parts) if parts else "unknown"


def _infer_video_range_from_codecs(codecs: str) -> str:
    return infer_video_range(codecs)


def _playlist_is_live(content: str) -> bool:
    """A playlist is live unless it explicitly signals termination via #EXT-X-ENDLIST or #EXT-X-PLAYLIST-TYPE:VOD."""
    if "#EXT-X-ENDLIST" in content:
        return False

    if re.search(r"#EXT-X-PLAYLIST-TYPE:\s*VOD", content):
        return False

    return True


class HLSParser:
    def __init__(self, m3u8_url: str, headers: dict[str, str] = None, content: str | None = None, has_drm: bool = False):
        self.m3u8_url = m3u8_url
        self.headers = headers or {}
        self._injected = content
        self.raw_content: str | None = content
        self._base_url = calc_base_url(m3u8_url)
        self.has_drm = has_drm

    def fetch_manifest(self) -> bool:
        start_parsing_time = time.time()

        if self._injected:
            self.raw_content = self._injected
            return True

        if self.m3u8_url.startswith("file://"):
            try:
                from urllib.request import url2pathname

                local_path = Path(url2pathname(urlparse(self.m3u8_url).path))
                self.raw_content = local_path.read_text(encoding="utf-8")
                self._base_url = local_path.parent.as_uri() + "/"
                logger.info(f"HlsParser:  parsed in {time.time() - start_parsing_time:.2f}s")
                return True
            except Exception as exc:
                console.print(f"[red]Failed to read local HLS manifest: {exc}.")
                logger.error(f"HLSParser: local file read failed: {exc}")
                return False

        try:
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=_request_timeout(), follow_redirects=True) as c:
                r = c.get(self.m3u8_url)
                r.raise_for_status()
                self.raw_content = r.text
            logger.info(f"HlsParser: fetched and parsed in {time.time() - start_parsing_time:.2f}s")
            return True
        except Exception as exc:
            console.print(f"[red]Failed to fetch HLS manifest: {exc}.")
            logger.error(f"HLSParser: fetch failed: {exc}")
            return False

    def save_raw(self, directory: Path) -> Path:
        return save_raw_manifest(self.raw_content, directory, "raw.m3u8")

    def parse_streams(self) -> list[Stream]:
        """Parse the master playlist into Stream objects."""
        if not self.raw_content:
            return []

        master_drm = self._parse_drm_tags(self.raw_content)
        streams: list[Stream] = []
        seen_ids: set = set()  # Track seen stream IDs to avoid duplicates (different CDN pathways)
        lines = self.raw_content.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # ── Video variant
            if line.startswith("#EXT-X-STREAM-INF:"):
                stream = self._parse_stream_inf(line)
                stream.drm = master_drm
                stream.format = "hls"
                if i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if nxt and not nxt.startswith("#"):
                        stream.playlist_url = urljoin(self._base_url, nxt)
                        if not stream.id:
                            stream.id = _make_video_id(stream)

                        # Skip duplicates: the same video variant (STABLE-VARIANT-ID)
                        # is listed once per audio group, producing identical-codec entries.
                        if stream.id not in seen_ids:
                            seen_ids.add(stream.id)
                            streams.append(stream)
                            logger.info(f"{stream}")
                i += 2
                continue

            # ── Audio / subtitle / CC rendition
            if line.startswith("#EXT-X-MEDIA:"):
                typ = self._attr(line, "TYPE", "").upper()
                if typ == "AUDIO":
                    s = self._parse_media_tag(line, "audio", master_drm)
                    if s:
                        # Skip duplicates: same STABLE-RENDITION-ID for different CDN pathways
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"{s}")

                elif typ == "SUBTITLES":
                    s = self._parse_media_tag(line, "subtitle", master_drm)
                    if s:
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"{s}")

                elif typ == "CLOSED-CAPTIONS":
                    s = self._parse_media_tag(line, "subtitle", master_drm)
                    if s:
                        s.is_cc = True
                        instream_id = self._attr(line, "INSTREAM-ID", "")
                        if instream_id and not s.id:
                            s.id = instream_id
                        if s.name and "[CC]" not in s.name:
                            s.name = f"{s.name} [CC]"
                        elif not s.name:
                            s.name = "[CC]"
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"{s}")

            i += 1

        if not any(s.type == "video" for s in streams):
            streams = self._variant_fallback(streams, master_drm)

        # Resolve child media playlists to extract the real DRM PSSH/KID and the live/VOD flag.
        self._resolve_drm(streams, master_drm)

        for stream in streams:
            enc_method = (stream.encryption_method or "").lower().replace("_", "-") if stream.encryption_method else ""

            if enc_method in ("aes-128", "aes-128-cbc"):
                # Whole-segment AES-128 (almost always MPEG-TS): the in-flight
                # fragment-MP4 decryptor can't handle it — it has to go through the
                # dedicated per-segment AES path in the post-download pass.
                stream.supports_live_decryption = False
                logger.debug(f"Stream {stream.id}: AES-128 - post-download decrypt only")
            elif enc_method.startswith("sample-aes") or enc_method in ("cbcs", "cbc1", "cens", "cenc"):
                stream.supports_live_decryption = True
                logger.debug(f"Stream {stream.id}: {enc_method or 'CENC'} - live per-segment decryption")
            else:
                # clear, or fMP4 CENC signalled elsewhere
                stream.supports_live_decryption = True

        manifest_live = any(s.is_live for s in streams)
        logger.info(f"HLS manifest type: {'LIVE' if manifest_live else 'VOD'}")

        if manifest_live:
            for stream in streams:
                if not stream.is_live and stream.playlist_url and stream.type != "video":
                    stream.is_live = True
                    logger.info(f"HLS stream marked as live (propagated): {stream}")

        return streams

    def parse_variant(self, variant_url: str) -> tuple[DRMInfo, str | None]:
        """Fetch and parse a variant playlist to find additional DRM info."""
        try:
            logger.info(f"HLSParser: fetching variant playlist {variant_url}")
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=_request_timeout(), follow_redirects=True) as c:
                r = c.get(variant_url)
                r.raise_for_status()
                variant_content = r.text
                return self._parse_drm_tags(variant_content), variant_content
        except Exception as exc:
            logger.error(f"HLSParser: parse_variant failed for {variant_url}: {exc}")
            return DRMInfo(), None

    @staticmethod
    def _drm_group_key(stream: Stream) -> str:
        """Group streams that share the same content key so we only fetch one child playlist per key."""
        url = stream.playlist_url or ""
        try:
            key_info = parse_qs(urlparse(url).query).get("keyInfo")
        except ValueError:
            key_info = None
        if key_info and key_info[0]:
            return f"keyInfo:{key_info[0]}"
        return f"url:{url}"

    def _resolve_drm(self, streams: list[Stream], master_drm: DRMInfo) -> None:
        """Fetch one child playlist per distinct key group to extract the real DRM method/PSSH/KID,
        then apply it to every stream sharing that key group."""
        advertised = master_drm.get_all_drm_types() if master_drm else []

        # Skip renditions that already carry real DRM (e.g. media-playlist fallback)
        # and the manifest itself (self-referential fallback URL).
        targets = [
            s
            for s in streams
            if s.type in ("video", "audio")
            and s.playlist_url
            and s.playlist_url != self.m3u8_url
            and not (s.drm and s.drm.is_encrypted())
        ]

        if not targets:
            return

        if not self.has_drm:
            # No DRM expected for this stream: fetch one representative child playlist
            # for live/VOD detection AND check for DRM (the caller may have been
            # unaware that this stream is protected).
            representative = next((s for s in targets if s.type == "video"), targets[0])
            logger.info(f"HLSParser: has_drm=False, fetching single representative playlist for live/VOD detection: {representative.playlist_url}")
            variant_drm, variant_content = self.parse_variant(representative.playlist_url or "")
            is_live = _playlist_is_live(variant_content) if variant_content is not None else None

            if variant_drm and variant_drm.is_encrypted():
                for dt in advertised:
                    variant_drm.add_advertised_type(dt)
                for s in targets:
                    s.drm = variant_drm
                logger.info(f"HLSParser: detected DRM in variant despite has_drm=False — {variant_drm!r}")

            if is_live is not None:
                for s in targets:
                    s.is_live = is_live
            return

        groups: dict[str, list[Stream]] = {}
        for s in targets:
            groups.setdefault(self._drm_group_key(s), []).append(s)
        representatives = [members[0] for members in groups.values()]

        logger.info(f"HLSParser: {len(groups)} DRM key group(s) detected:")
        for idx, (_key, members) in enumerate(groups.items(), 1):
            rep = members[0]
            member_ids = [f"{m.type}:{m.id}" for m in members]
            logger.info(f"  Group {idx}: rep={rep.id!r} | {rep.resolution or rep.language or '?'} | {len(members)} stream(s): {member_ids}")

        def _resolve(stream: Stream):
            variant_drm, variant_content = self.parse_variant(stream.playlist_url or "")
            return stream, variant_drm, variant_content

        with ThreadPoolExecutor(max_workers=min(8, len(representatives))) as ex:
            for rep, variant_drm, variant_content in ex.map(_resolve, representatives):
                members = groups[self._drm_group_key(rep)]
                is_live = _playlist_is_live(variant_content) if variant_content is not None else None

                if variant_drm and variant_drm.is_encrypted():
                    for dt in advertised:
                        variant_drm.add_advertised_type(dt)
                    kid = variant_drm.get_kid_display() or "?"
                    logger.info(f"HLSParser: resolved variant — rep_id={rep.id!r} | KID={kid} | {variant_drm.get_drm_display()}")

                for member in members:
                    if is_live is not None:
                        member.is_live = is_live
                    if variant_drm and variant_drm.is_encrypted():
                        member.drm = variant_drm

    def _parse_stream_inf(self, line: str) -> Stream:
        s = Stream(type="video", format="hls")

        stable_id = self._attr(line, "STABLE-VARIANT-ID", "")
        if stable_id:
            s.id = stable_id

        m = re.search(r"(?<![A-Z-])BANDWIDTH=(\d+)", line)
        if m:
            s.bitrate = int(m.group(1))

        m = re.search(r"AVERAGE-BANDWIDTH=(\d+)", line)
        if m:
            s.avg_bitrate = int(m.group(1))
            s.bitrate = s.avg_bitrate  # Override if AVERAGE-BANDWIDTH is present, as it's more accurate

        m = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
        if m:
            s.width = int(m.group(1))
            s.height = int(m.group(2))
            s.resolution = f"{s.width}x{s.height}"

        m = re.search(r"FRAME-RATE=([\d.]+)", line)
        if m:
            s.fps = m.group(1)

        m = re.search(r'CODECS="([^"]+)"', line)
        if m:
            s.codecs = m.group(1)

        # An #EXT-X-STREAM-INF that declares CODECS but none of them is a video
        # codec, and carries no RESOLUTION, is an audio-only variant (e.g.
        # Unified Streaming's ".../...-audio=65000.m3u8"). Leaving it typed as
        # "video" makes `-sv worst` pick it and download an audio-only file.
        if s.codecs and not s.resolution:
            codec_tokens = [c.strip().lower() for c in s.codecs.split(",") if c.strip()]
            if codec_tokens and not any(
                tok.startswith(VIDEO_CODEC_PREFIXES) for tok in codec_tokens
            ):
                s.type = "audio"

        vr = self._attr(line, "VIDEO-RANGE", "").upper()
        s.video_range = vr if vr else _infer_video_range_from_codecs(s.codecs)

        hdcp = self._attr(line, "HDCP-LEVEL", "").upper()
        if hdcp:
            s.hdcp_level = hdcp

        return s

    def _parse_media_tag(self, line: str, stream_type: str, drm: DRMInfo) -> Stream | None:
        s = Stream(type=stream_type, format="hls")
        s.drm = drm

        stable_id = self._attr(line, "STABLE-RENDITION-ID", "")
        group_id = self._attr(line, "GROUP-ID", "")
        lang = self._attr(line, "LANGUAGE", "")
        name = self._attr(line, "NAME", "")

        if lang:
            lang_m = _COMPOUND_LANG_RE.match(lang)
            if lang_m:
                base_lang = lang_m.group(1)
                lang_suffix = lang_m.group(2).lower()
            else:
                base_lang = lang
                lang_suffix = ""
            s.language = lang  # preserve original for filename generation
            s.resolved_language = resolve_locale(base_lang)
        else:
            lang_suffix = ""
        if name:
            s.name = name

        s.id = stable_id if stable_id else _make_rendition_id(group_id, lang, name)

        ch = self._attr(line, "CHANNELS", "")
        if ch:
            s.channels = ch

        uri = self._attr(line, "URI", "")
        if uri:
            s.playlist_url = urljoin(self._base_url, uri)

        s.default = self._attr(line, "DEFAULT", "NO").upper() == "YES"
        s.autoselect = self._attr(line, "AUTOSELECT", "NO").upper() == "YES"
        s.forced = self._attr(line, "FORCED", "NO").upper() == "YES"

        if not s.bitrate:
            m = re.search(r"audio-(?:HE2-)?stereo-(\d+)", group_id)
            if m:
                s.bitrate = int(m.group(1)) * 1000
            elif "audio-ac3" in group_id:
                s.bitrate = 384_000
            elif "audio-atmos" in group_id:
                s.bitrate = 2_448_000

        if not s.forced and stream_type == "subtitle":
            if lang_suffix == "forced":
                s.forced = True
            elif name and _FORCED_NAME_RE.search(name):
                s.forced = True

        if s.forced:
            s.default = False

        assoc = self._attr(line, "ASSOC-LANGUAGE", "")
        if assoc:
            s.assoc_language = assoc

        chars = self._attr(line, "CHARACTERISTICS", "")
        if chars:
            s.accessibility = chars
            if "describes-music-and-sound" in chars.lower() or "hearing" in chars.lower():
                s.is_sdh = True

        # is_cc: detect from NAME for TYPE=SUBTITLES
        if not s.is_cc and name and _CC_NAME_RE.search(name):
            s.is_cc = True

        # is_sdh: detect from NAME if not already set via CHARACTERISTICS
        if not s.is_sdh and name and _SDH_NAME_RE.search(name):
            s.is_sdh = True

        # Name annotation for display (non-destructive)
        if s.forced and "[Forced]" not in (s.name or ""):
            s.name = f"{s.name} [Forced]" if s.name else "[Forced]"

        return s

    def _variant_fallback(self, existing: list[Stream], drm: DRMInfo) -> list[Stream]:
        total_dur = 0.0
        bandwidth = 0
        for line in (self.raw_content or "").splitlines():
            line = line.strip()
            if line.startswith("#EXTINF:"):
                m = re.search(r"#EXTINF:([\d.]+)", line)
                if m:
                    total_dur += float(m.group(1))
            elif line.startswith("#EXT-X-STREAM-INF:"):
                m = re.search(r"BANDWIDTH=(\d+)", line)
                if m:
                    bandwidth = int(m.group(1))

        s = Stream(type="video", format="hls")
        s.bitrate = bandwidth
        s.duration = total_dur
        s.drm = drm
        s.playlist_url = self.m3u8_url
        s.id = _make_video_id(s)
        s.is_live = (total_dur > 0) and _playlist_is_live(self.raw_content or "")
        logger.info(f"{s}")
        return [s] + existing

    def _parse_drm_tags(self, content: str) -> DRMInfo:
        info = DRMInfo()

        for cpc in re.findall(r'ALLOWED-CPC="([^"]+)"', content, re.IGNORECASE):
            for token in cpc.split(","):
                t = token.strip().lower()
                if not t:
                    continue

                detected = DRMType.from_scheme(t)
                if detected != DRMType.UNKNOWN:
                    info.add_advertised_type(detected)

        # Any #EXT-X-KEY METHOD, not just the AES-128/AES-256 family — this also catches SAMPLE-AES/SAMPLE-AES-CTR/SAMPLE-AES-CENC
        method_m = re.search(r'#EXT-X-(?:SESSION-)?KEY:.*?METHOD=([^,"\s]+)', content, re.IGNORECASE)
        if method_m and method_m.group(1).upper() != "NONE":
            info.method = method_m.group(1)

        # Capture the whole attribute list of each key line, then pull URI out of
        # it — attributes such as KEYFORMAT="urn:uuid:edef8ba9-..." routinely come
        # *after* URI="...", so a pre-URI-only capture misses the DRM-system hint.
        key_line_re = re.compile(r'#EXT-X-(?:SESSION-)?KEY:([^\r\n]+)', re.IGNORECASE)
        uri_re = re.compile(r'URI="([^"]+)"', re.IGNORECASE)

        for attrs in key_line_re.findall(content):
            uri_m = uri_re.search(attrs)
            if not uri_m:
                continue
            full_uri = uri_m.group(1)

            # KEYID=0x<hex> on the key line is the content KID. Widevine key
            # extraction filters the licensed keys by this KID, so losing it
            # here means "no key for required KID" downstream.
            keyid_m = re.search(r'KEYID=0x([0-9A-Fa-f]{16,})', attrs, re.IGNORECASE)
            if keyid_m and not info.kid:
                info.set_kid(keyid_m.group(1))

            try:
                if full_uri.startswith("data:"):
                    b64 = full_uri.split(",", 1)[-1].strip()
                    b64 = b64.split(";")[0].split('"')[0].strip()

                    try:
                        decoded = base64.b64decode(b64)
                    except binascii.Error:
                        b64c = re.sub(r"[^A-Za-z0-9+/=]", "", b64)
                        while len(b64c) % 4 != 0:
                            b64c += "="
                        decoded = base64.b64decode(b64c)

                    # Canonical base64 to avoid padding issues downstream.
                    b64 = base64.b64encode(decoded).decode("ascii")

                    # Check if it's JSON
                    try:
                        js = json.loads(decoded)
                        key_list = []
                        if isinstance(js, list):
                            key_list = js

                        for k in key_list:
                            sys = (k.get("system") or k.get("keyformat") or "").lower()
                            pssh = k.get("pssh")
                            kid = k.get("id")

                            if "widevine" in sys:
                                if pssh:
                                    info.set_pssh(pssh, DRMType.WIDEVINE, key_uri=full_uri)
                            elif "playready" in sys:
                                if pssh:
                                    info.set_pssh(pssh, DRMType.PLAYREADY, key_uri=full_uri)
                            elif "streamingkeydelivery" in sys or "fairplay" in sys:
                                info.method = "SAMPLE-AES"
                                uri = k.get("uri")
                                if uri:
                                    info.set_pssh(uri, DRMType.FAIRPLAY, key_uri=full_uri)

                            if kid and not info.kid:
                                info.kid = kid

                    except (json.JSONDecodeError, TypeError, AttributeError, UnicodeDecodeError, ValueError):
                        is_wv = ("edef8ba9" in attrs.lower() or "edef8ba9" in full_uri.lower() or "widevine" in attrs.lower())
                        is_pr = ("9a04f079" in attrs.lower() or "9a04f079" in full_uri.lower() or "playready" in attrs.lower() or "com.microsoft" in attrs.lower())
                        if not is_pr and not is_wv:
                            try:
                                xml_text = decoded.decode("utf-16-le", errors="ignore")
                                if "<WRMHEADER" in xml_text or "<KID" in xml_text:
                                    is_pr = True
                            except Exception:
                                pass

                        if is_wv:
                            info.set_pssh(b64, DRMType.WIDEVINE, key_uri=full_uri)

                        elif is_pr:
                            kid = _DRMSystems.extract_kid_from_playready_pro(b64)
                            if kid:
                                info.set_kid(kid)
                                logger.debug(f"PlayReady WRM Header KID extracted: {kid}")
                            info.set_pssh(b64, DRMType.PLAYREADY, key_uri=full_uri)
                        else:
                            info.set_pssh(b64, key_uri=full_uri)

                elif full_uri.startswith("skd:"):
                    info.method = "SAMPLE-AES"
                    info.set_pssh(full_uri, DRMType.FAIRPLAY, key_uri=full_uri)

            except Exception as exc:
                logger.error(f"HLSParser DRM probe error: {exc}")

        return info

    def get_drm_info(self) -> dict:
        if not self.raw_content:
            return {"widevine": [], "playready": [], "fairplay": []}
        return self._parse_drm_tags(self.raw_content).to_dict()

    @staticmethod
    def _attr(line: str, key: str, default: str = "") -> str:
        m = re.search(rf'{key}="([^"]*)"', line)
        if m:
            return m.group(1)
        m = re.search(rf"{key}=([^,\s]+)", line)
        if m:
            return m.group(1)
        return default
