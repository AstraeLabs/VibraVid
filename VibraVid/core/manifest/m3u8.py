# 13.03.26

import base64
import binascii
import json
import logging
import os
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

from rich.console import Console

from VibraVid.core.drm.system import _DRMSystems
from VibraVid.core.manifest._utils import calc_base_url, save_raw_manifest
from VibraVid.core.manifest.stream import DRMInfo, DRMType, Stream
from VibraVid.core.utils.codec import AUDIO_CODEC_PREFIXES, VIDEO_CODEC_PREFIXES, detect_stream_type, infer_video_range
from VibraVid.core.utils.language import resolve_locale
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers, get_with_retry

logger = logging.getLogger(__name__)
console = Console()

_CC_NAME_RE = re.compile(r"\[CC\]|\bCC\b|closed[- _]captions?|\bSDH\b", re.IGNORECASE)
_SDH_NAME_RE = re.compile(r"\[SDH\]|\bSDH\b|hearing[- _]impaired|\bHI\b", re.IGNORECASE)
_FORCED_NAME_RE = re.compile(r"\[forced\]|\bforced\b", re.IGNORECASE)
_COMPOUND_LANG_RE = re.compile(r"^(.+?)[-_.]\[?(forced|cc|sdh|hi|default)\]?$", re.IGNORECASE)
_KEY_LINE_RE = re.compile(r"#EXT-X-(?:SESSION-)?KEY:([^\r\n]+)", re.IGNORECASE)
_SESSION_KEY_LINE_RE = re.compile(r"#EXT-X-SESSION-KEY:([^\r\n]+)", re.IGNORECASE)
_KEY_URI_RE = re.compile(r'URI="([^"]+)"', re.IGNORECASE)


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


def _sniff_audio_codec_from_uri(uri: str) -> str:
    """Best-effort per-track codec hint from the rendition URI's filename."""
    filename = uri.rsplit("/", 1)[-1].lower()
    for token in filename.split("_"):
        if any(token.startswith(p) for p in AUDIO_CODEC_PREFIXES):
            return token
    return ""


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
        self._variant_drm_cache: dict[frozenset[str], DRMInfo] = {}

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
                effective_url = str(r.url)

            # The playlist host may 302 to a session/edge-specific CDN node --
            # relative segment/variant URLs must resolve against that final host.
            if effective_url and effective_url != self.m3u8_url:
                self._base_url = calc_base_url(effective_url)

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
        master_drm._inherited_from_master = True
        self._variant_drm_cache.clear()
        session_key_groups = self._parse_session_key_groups()
        if len(session_key_groups) > 1:
            logger.info(f"HLSParser: {len(session_key_groups)} #EXT-X-SESSION-KEY profile(s) in the master:")
            for chars, info in session_key_groups.items():
                label = ",".join(sorted(chars)) or "<no CHARACTERISTICS>"
                logger.info(f"  {label} -> KID={info.get_kid_display() or '?'} | {info.get_drm_display()}")

        streams: list[Stream] = []
        seen_ids: set = set()  # Track seen stream IDs to avoid duplicates (different CDN pathways)
        lines = self.raw_content.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # ── Video variant
            if line.startswith("#EXT-X-STREAM-INF:"):
                stream = self._parse_stream_inf(line)
                variant_chars = self._attr(line, "CHARACTERISTICS", "")
                stream.drm, stream._hls_key_profile = self._drm_for_variant(variant_chars, session_key_groups, master_drm)
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
                    s = self._parse_media_tag(line, "audio", self._unencrypted_drm(master_drm))
                    if s:
                        # Skip duplicates: same STABLE-RENDITION-ID for different CDN pathways
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"{s}")

                elif typ == "SUBTITLES":
                    s = self._parse_media_tag(line, "subtitle", self._unencrypted_drm(master_drm))
                    if s:
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"{s}")

                elif typ == "CLOSED-CAPTIONS":
                    s = self._parse_media_tag(line, "subtitle", self._unencrypted_drm(master_drm))
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

        self._backfill_audio_codecs(streams)

        return streams

    def _backfill_audio_codecs(self, streams: list[Stream]) -> None:
        """Populate the .codecs field of audio streams that don't have it, using the CODECS attribute of their parent #EXT-X-STREAM-INF line."""
        if not self.raw_content:
            return

        group_codec: dict[str, str] = {}
        for line in self.raw_content.splitlines():
            line = line.strip()
            if not line.startswith("#EXT-X-STREAM-INF:"):
                continue
            
            group_id = self._attr(line, "AUDIO", "")
            codecs = self._attr(line, "CODECS", "")
            if not (group_id and codecs) or group_id in group_codec:
                continue
            
            for tok in (c.strip() for c in codecs.split(",")):
                if detect_stream_type(tok) == "audio":
                    group_codec[group_id] = tok
                    break

        if not group_codec:
            return

        for stream in streams:
            if stream.type != "audio" or stream.codecs:
                continue
            group_id = getattr(stream, "_hls_group_id", "")
            if group_id in group_codec:
                stream.codecs = group_codec[group_id]

    def parse_variant(self, variant_url: str) -> tuple[DRMInfo, str | None]:
        """Fetch and parse a variant playlist to find additional DRM info."""
        try:
            logger.info(f"HLSParser: fetching variant playlist {variant_url}")
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=_request_timeout(), follow_redirects=True) as c:
                r = get_with_retry(c, variant_url)
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

        profile = getattr(stream, "_hls_key_profile", "")
        if profile:
            return f"chars:{profile}"
        
        group_id = getattr(stream, "_hls_group_id", "")
        if stream.type == "audio" and group_id:
            return f"group:{group_id}"

        return f"url:{url}"

    def _resolve_drm(self, streams: list[Stream], master_drm: DRMInfo) -> None:
        """Fetch one child playlist per distinct key group to extract the real DRM method/PSSH/KID,
        then apply it to every stream sharing that key group."""
        advertised = master_drm.get_all_drm_types() if master_drm else []
        targets = [
            s
            for s in streams
            if s.type in ("video", "audio")
            and s.playlist_url
            and s.playlist_url != self.m3u8_url
            and not (
                s.drm
                and s.drm.is_encrypted()
                and not getattr(s.drm, "_inherited_from_master", False)
            )
        ]

        if not targets:
            return

        groups: dict[str, list[Stream]] = {}
        for s in targets:
            groups.setdefault(self._drm_group_key(s), []).append(s)

        def _resolve(stream: Stream):
            """Read one group's media playlist and its init segment."""
            variant_drm, variant_content = self.parse_variant(stream.playlist_url or "")
            probe = self._probe_init_segment(variant_content, stream.playlist_url or "")
            return stream, variant_drm, variant_content, probe

        probed = None
        if not self.has_drm:
            representative = next((s for s in targets if s.type == "video"), targets[0])
            logger.info(f"HLSParser: has_drm=False, reading one representative playlist for live/VOD detection: {representative.playlist_url}")
            _, variant_drm, variant_content, probe = _resolve(representative)
            is_live = _playlist_is_live(variant_content) if variant_content is not None else None
            declared = bool(variant_drm and variant_drm.is_encrypted())

            if not declared and not (probe is not None and probe.encrypted):
                if is_live is not None:
                    for s in targets:
                        s.is_live = is_live
                return

            if declared:
                logger.info(f"HLSParser: detected DRM in variant despite has_drm=False — {variant_drm!r}")
            else:
                logger.info(f"HLSParser: init segment is encrypted though the playlist declares no key — KID={(probe.kid if probe else None) or '?'}")

            if len(groups) == 1:
                final = self._group_drm(representative, variant_drm, probe, master_drm, advertised)
                for s in targets:
                    if is_live is not None:
                        s.is_live = is_live
                    if final is not None:
                        s.drm = final
                return

            logger.info(f"HLSParser: DRM found and {len(groups)} key group(s) present — resolving each group")
            probed = (representative, variant_drm, variant_content, probe)

        representatives = [members[0] for members in groups.values()]
        if probed is not None:
            probe_key = self._drm_group_key(probed[0])
            representatives = [r for r in representatives if self._drm_group_key(r) != probe_key]

        logger.info(f"HLSParser: {len(groups)} DRM key group(s) detected:")
        for idx, (_key, members) in enumerate(groups.items(), 1):
            rep = members[0]

            # A profile-keyed group can hold the whole ladder, so list a sample, not all of it.
            member_ids = [f"{m.type}:{m.id}" for m in members[:6]]
            if len(members) > len(member_ids):
                member_ids.append(f"… +{len(members) - len(member_ids)} more")
            logger.info(f"  Group {idx}: rep={rep.id!r} | {rep.resolution or rep.language or '?'} | {len(members)} stream(s): {member_ids}")

        resolved = [probed] if probed is not None else []
        if representatives:
            with ThreadPoolExecutor(max_workers=min(8, len(representatives))) as ex:
                resolved.extend(ex.map(_resolve, representatives))

        for rep, variant_drm, variant_content, probe in resolved:
            members = groups[self._drm_group_key(rep)]
            is_live = _playlist_is_live(variant_content) if variant_content is not None else None
            final = self._group_drm(rep, variant_drm, probe, master_drm, advertised)

            for member in members:
                if is_live is not None:
                    member.is_live = is_live
                if final is not None:
                    member.drm = final

    def _group_drm(self, rep: Stream, variant_drm: DRMInfo, probe, master_drm: DRMInfo, advertised: list[str]) -> DRMInfo | None:
        """The DRM to stamp on one key group, most authoritative source first."""
        resolved = variant_drm if (variant_drm and variant_drm.is_encrypted()) else None
        if resolved is not None:
            for dt in advertised:
                resolved.add_advertised_type(dt)

        def _log(drm: DRMInfo, source: str) -> DRMInfo:
            logger.info(f"HLSParser: resolved variant — rep_id={rep.id!r} | KID={drm.get_kid_display() or '?'} | {drm.get_drm_display()} | from {source}")
            return drm

        if probe is None:
            # No init segment, or no flux to read it with: the playlist is all we have.
            return _log(resolved, "playlist #EXT-X-KEY") if resolved is not None else None

        declared = resolved or (rep.drm if rep.drm and rep.drm.is_encrypted() else None)
        declared_kids = declared.get_all_kids() if declared else []

        if not probe.encrypted:
            if declared is not None:
                logger.warning(f"HLSParser: {rep.id!r} — init segment is not encrypted but the manifest declared KID(s) {','.join(declared_kids) or '(none)'}; trusting the init segment")
            return self._unencrypted_drm(master_drm)

        if not probe.kid:
            # Encrypted but no tenc KID (e.g. a fixed-key stream): keep the manifest's.
            return _log(resolved, "playlist #EXT-X-KEY") if resolved is not None else None

        if [probe.kid] != declared_kids:
            logger.info(f"HLSParser: {rep.id!r} — tenc default_kid={probe.kid} replaces manifest KID(s) {','.join(declared_kids) or '(none)'}")

        final = self._pin_kid(declared or master_drm, probe.kid)
        for dt in advertised:
            final.add_advertised_type(dt)
        return _log(final, "init segment tenc")

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

        # (?<!-) so a SUPPLEMENTAL-CODECS listed before CODECS can't be picked up here
        m = re.search(r'(?<!-)CODECS="([^"]+)"', line)
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

        s.supplemental_codecs = self._attr(line, "SUPPLEMENTAL-CODECS", "")
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
        s._hls_group_id = group_id  # consumed by _backfill_audio_codecs, not part of the public Stream API

        ch = self._attr(line, "CHANNELS", "")
        if ch:
            s.channels = ch

        uri = self._attr(line, "URI", "")
        if uri:
            s.playlist_url = urljoin(self._base_url, uri)

        if stream_type == "audio" and uri:
            hint = _sniff_audio_codec_from_uri(uri)
            if hint:
                logger.info(f"HLSParser: audio codec hint from URI: {hint} for rendition {s.id}")
                s.codecs = hint

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

        # is_cc: detect from the LANGUAGE suffix or NAME for TYPE=SUBTITLES.
        if not s.is_cc and lang_suffix == "cc":
            s.is_cc = True
        if not s.is_cc and name and _CC_NAME_RE.search(name):
            s.is_cc = True

        # is_sdh: detect from the LANGUAGE suffix, NAME, or CHARACTERISTICS
        if not s.is_sdh and lang_suffix == "sdh":
            s.is_sdh = True
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

    def _apply_key_line(self, info: DRMInfo, attrs: str) -> None:
        """Decode one #EXT-X-KEY / #EXT-X-SESSION-KEY attribute list into *info* (PSSH, KID, method)."""
        uri_m = _KEY_URI_RE.search(attrs)
        if not uri_m:
            return
        
        full_uri = uri_m.group(1)
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
                        kid = k.get("id") or k.get("key-id")

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

                        if kid:
                            info.set_kid(kid)

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
                        kids = _DRMSystems.extract_kids_from_playready_pro(b64)
                        for kid in kids:
                            info.set_kid(kid)
                        if kids:
                            logger.debug(f"PlayReady WRM Header KID(s) extracted: {kids}")
                        info.set_pssh(b64, DRMType.PLAYREADY, key_uri=full_uri)
                    else:
                        info.set_pssh(b64, key_uri=full_uri)

            elif full_uri.startswith("skd:"):
                info.method = "SAMPLE-AES"
                info.set_pssh(full_uri, DRMType.FAIRPLAY, key_uri=full_uri)

        except Exception as exc:
            logger.error(f"HLSParser DRM probe error: {exc}")

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

        for attrs in _KEY_LINE_RE.findall(content):
            self._apply_key_line(info, attrs)

        return info

    def _parse_session_key_groups(self) -> dict[frozenset[str], DRMInfo]:
        """Bucket the master's #EXT-X-SESSION-KEY lines by their CHARACTERISTICS set."""
        groups: dict[frozenset[str], DRMInfo] = {}
        for attrs in _SESSION_KEY_LINE_RE.findall(self.raw_content or ""):
            chars = frozenset(self._characteristics_set(self._attr(attrs, "CHARACTERISTICS", "")))
            info = groups.get(chars)
            if info is None:
                info = groups[chars] = DRMInfo()
                method_m = re.search(r'METHOD=([^,"\s]+)', attrs)
                if method_m and method_m.group(1).upper() != "NONE":
                    info.method = method_m.group(1)
            self._apply_key_line(info, attrs)

        return {chars: info for chars, info in groups.items() if info.is_encrypted()}

    @staticmethod
    def _unencrypted_drm(master_drm: DRMInfo) -> DRMInfo:
        """A fresh, unencrypted DRMInfo carrying only the DRM systems the master advertises."""
        info = DRMInfo()
        for drm_type in master_drm.get_all_drm_types():
            info.add_advertised_type(drm_type)
        return info

    @staticmethod
    def _pin_kid(source: DRMInfo, kid: str, scheme: str | None = None) -> DRMInfo:
        """Copy *source*'s PSSH/DRM systems but with *kid* as the one and only KID."""
        info = DRMInfo()
        info.method = scheme or source.method
        for drm_type in source.get_all_drm_types():
            psshs = source.get_all_pssh_for(drm_type)
            for pssh in psshs:
                info.set_pssh(pssh, drm_type, key_uri=source.get_key_uri(drm_type, pssh))
            if not psshs:
                info.add_advertised_type(drm_type)
        info.set_kid(kid)
        return info

    def _probe_init_segment(self, variant_content: str | None, playlist_url: str):
        """Read a media playlist's #EXT-X-MAP init segment and let flux report the truth."""
        if not variant_content or not playlist_url:
            return None

        m = re.search(r'#EXT-X-MAP:[^\r\n]*?URI="([^"]+)"', variant_content, re.IGNORECASE)
        if not m:
            return None
        init_url = urljoin(calc_base_url(playlist_url), m.group(1))

        tmp_path = None
        try:
            from VibraVid.core.decryptor._models import _parse_flux_json, _run_flux_dump

            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=_request_timeout(), follow_redirects=True) as c:
                data = get_with_retry(c, init_url).content
            if not data:
                return None

            suffix = Path(urlparse(init_url).path).suffix or ".mp4"
            with tempfile.NamedTemporaryFile(prefix="vv_init_", suffix=suffix, delete=False) as fh:
                fh.write(data)
                tmp_path = fh.name

            report = _run_flux_dump(tmp_path)
            if report is None:
                return None
            return _parse_flux_json(report)
        except Exception as exc:
            logger.debug(f"HLSParser: init segment probe failed for {init_url}: {exc}")
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _characteristics_set(chars: str) -> set[str]:
        return {c.strip().lower() for c in chars.split(",") if c.strip()}

    def _drm_for_variant(
        self, chars: str, groups: dict[frozenset[str], DRMInfo], master_drm: DRMInfo
    ) -> tuple[DRMInfo, str]:
        """The master-level DRM that applies to one #EXT-X-STREAM-INF, narrowed by CHARACTERISTICS."""
        wanted = self._characteristics_set(chars)
        if not wanted or not groups:
            return master_drm, ""

        matching_keys = [key for key in groups if key & wanted]
        if not matching_keys:
            return master_drm, ""

        narrowest = min(len(key) for key in matching_keys)
        matched = [groups[key] for key in matching_keys if len(key) == narrowest]

        signature = frozenset(wanted)
        profile = ",".join(sorted(signature))
        cached = self._variant_drm_cache.get(signature)
        if cached is not None:
            return cached, profile

        if len(matched) == 1:
            merged = matched[0]
        else:
            merged = DRMInfo()
            for info in matched:
                self._merge_drm(merged, info)

        merged._inherited_from_master = True
        self._variant_drm_cache[signature] = merged
        return merged, profile

    @staticmethod
    def _merge_drm(target: DRMInfo, source: DRMInfo) -> None:
        """Fold *source* into *target*, preserving per-system PSSH and KID order."""
        if source.method and not target.method:
            target.method = source.method
        for drm_type in source.get_all_drm_types():
            for pssh in source.get_all_pssh_for(drm_type):
                target.set_pssh(pssh, drm_type, key_uri=source.get_key_uri(drm_type, pssh))
            if not source.get_all_pssh_for(drm_type):
                target.add_advertised_type(drm_type)
        for kid in source.get_all_kids():
            target.set_kid(kid)

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
