# 25.07.26

import base64
import json
import logging
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console

from VibraVid.core.drm.system import DRMType
from VibraVid.core.manifest._utils import calc_base_url, fast_urljoin_auto, save_raw_manifest
from VibraVid.core.manifest.stream import Segment, Stream
from VibraVid.core.utils.language import resolve_locale
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

console = Console()
logger = logging.getLogger(__name__)

MANIFEST_MARKER = "vibravid_manifest"
_PLACEHOLDER_RE = re.compile(r"\$\$|\$([A-Za-z]+)(%0\d+[diouxX])?\$")
_MEDIA_TYPES = ("video", "audio", "subtitle")


def is_custom_manifest(text: str) -> bool:
    """True if *text* looks like a custom manifest (cheap check, no full parse)."""
    return MANIFEST_MARKER in (text or "")[:4096]


def substitute(
    template: str, number: int | None = None, time_value: float | None = None, representation_id: str = ""
) -> str:
    """Replace DASH-style ``$Name$`` / ``$Name%0Nd$`` placeholders in *template*."""

    def _repl(match: re.Match) -> str:
        if match.group(0) == "$$":  # DASH escape for a literal '$'
            return "$"

        name, fmt = match.group(1), match.group(2)
        lowered = name.lower()
        if lowered == "number":
            value: Any = number
        elif lowered == "time":
            value = time_value
        elif lowered == "representationid":
            return representation_id
        else:
            return match.group(0)  # unknown placeholder: leave verbatim

        if value is None:
            return match.group(0)

        if fmt:
            return fmt % int(value)

        # A whole float would render as "6.0" and break most CDNs
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    return _PLACEHOLDER_RE.sub(_repl, template)


class CustomParser:
    def __init__(self, url: str, headers: dict[str, str] | None = None, content: str | None = None):
        self.url = url
        self.headers = headers or {}
        self._injected = content
        self.raw_content: str | None = content
        self._data: dict[str, Any] | None = None
        self._base_url = calc_base_url(url) if "://" in (url or "") else ""

    @staticmethod
    def _local_path(url: str) -> Path:
        """Local filesystem path for a ``file://`` URI or a plain path (Windows-safe)."""
        if url.startswith("file://"):
            from urllib.request import url2pathname

            return Path(url2pathname(urlparse(url).path))
        return Path(url)

    def fetch_manifest(self) -> bool:
        """Load the manifest JSON from the injected content, a local path, or the network."""
        try:
            if self._injected:
                self.raw_content = self._injected
            elif not urlparse(self.url).scheme.startswith("http"):
                local = self._local_path(self.url)
                self.raw_content = local.read_text(encoding="utf-8")
                self._base_url = local.parent.as_uri() + "/"
            else:
                timeout = config_manager.config.get_int("REQUESTS", "timeout")
                hdrs = dict(self.headers)
                hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
                with create_client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
                    resp = c.get(self.url)
                    resp.raise_for_status()
                    self.raw_content = resp.text

            self._data = json.loads(self.raw_content or "")

        except Exception as exc:
            console.print(f"[red]Error reading custom manifest: {exc}")
            logger.error(f"CustomParser: fetch/parse failed: {exc}")
            return False

        if not isinstance(self._data, dict):
            console.print("[red]Custom manifest must be a JSON object.")
            return False

        declared = self._data.get("base_url")
        if declared:
            self._base_url = declared if declared.endswith("/") else declared + "/"

        tracks = self._data.get("tracks")
        if not isinstance(tracks, list) or not tracks:
            console.print("[red]Custom manifest has no 'tracks' array.")
            logger.error("CustomParser: missing or empty 'tracks'")
            return False

        logger.info(f"CustomParser: loaded manifest with {len(tracks)} track(s), base_url={self._base_url!r}")
        return True

    def save_raw(self, directory: Path) -> Path:
        return save_raw_manifest(self.raw_content or "", directory, "raw.custom.json")

    def parse_streams(self) -> list[Stream]:
        if not self._data:
            return []

        global_duration = float(self._data.get("duration") or 0.0)
        streams: list[Stream] = []

        for idx, raw_track in enumerate(self._data.get("tracks") or []):
            try:
                stream = self._build_stream(idx, raw_track, global_duration)
            except Exception as exc:
                console.print(f"[yellow]Skipping track {idx}: {exc}")
                logger.error(f"CustomParser: track {idx} failed: {exc}")
                continue

            if stream is not None:
                streams.append(stream)

        return streams

    def _build_stream(self, idx: int, track: dict[str, Any], global_duration: float) -> Stream | None:
        if not isinstance(track, dict):
            raise ValueError("track entry is not an object")

        stype = str(track.get("type") or "").strip().lower()
        if stype not in _MEDIA_TYPES:
            raise ValueError(f"unsupported track type {stype!r} (expected one of {', '.join(_MEDIA_TYPES)})")

        s = Stream(type=stype)
        s.id = str(track.get("id") or f"custom{idx}")
        s.codecs = str(track.get("codecs") or "")
        s.bitrate = int(track.get("bitrate") or 0)
        s.avg_bitrate = int(track.get("avg_bitrate") or 0)
        s.duration = float(track.get("duration") or global_duration or 0.0)
        s.name = str(track.get("name") or "")
        s.label = str(track.get("label") or "")
        s.format = str(track.get("format") or "")

        if stype == "video":
            s.width = int(track.get("width") or 0)
            s.height = int(track.get("height") or 0)
            s.fps = str(track.get("fps") or "")
            s.video_range = str(track.get("video_range") or "")
            if s.width and s.height:
                s.resolution = f"{s.width}x{s.height}"
        else:
            lang_raw = str(track.get("language") or "und")
            s.language = lang_raw
            s.resolved_language = resolve_locale(lang_raw) if lang_raw and lang_raw != "und" else ""

        if stype == "audio":
            s.channels = str(track.get("channels") or "")
            s.sample_rate = int(track.get("sample_rate") or 0)
            s.default = bool(track.get("default"))

        if stype == "subtitle":
            s.forced = bool(track.get("forced"))
            s.is_cc = bool(track.get("is_cc"))
            s.is_sdh = bool(track.get("is_sdh"))

        drm = track.get("drm")
        if isinstance(drm, dict) and drm.get("kid"):
            s.drm.set_kid(str(drm["kid"]))
            if drm.get("type"):
                s.drm.display_type = str(drm["type"])  # cosmetic only, see DRMInfo.display_type

        pssh = drm.get("pssh") if isinstance(drm, dict) else None
        if pssh:
            dt = DRMType.from_scheme(drm.get("type"))
            if dt == DRMType.UNKNOWN:
                dt = DRMType.WIDEVINE
            try:
                s.drm.set_pssh(str(pssh), drm_type_hint=dt)
            except Exception as exc:
                logger.debug(f"CustomParser: failed to set pssh for track {s.id}: {exc}")

        number = 0
        init_seg = self._build_init(track.get("init"))
        if init_seg is not None:
            init_seg.number = number
            s.add_segment(init_seg)
            number += 1

        media = self._build_media(track.get("segments"), s, start_number=number)
        if not media:
            raise ValueError("track has no segments")

        for seg in media:
            s.add_segment(seg)

        s.compute_estimated_size()
        logger.info(f"CustomParser: track {s.id} [{stype}] {s.resolution or s.language} — {len(media)} segment(s), init={'yes' if init_seg else 'no'}")
        return s

    def _abs(self, ref: str) -> str:
        """Resolve *ref* against the manifest base URL (absolute refs pass through)."""
        ref = str(ref or "").strip()
        if not ref or "://" in ref:
            return ref
        if not self._base_url:
            return ref
        return fast_urljoin_auto(self._base_url, ref)

    def _build_init(self, init: Any) -> Segment | None:
        if not init:
            return None

        if isinstance(init, str):
            init = {"url": init}
        if not isinstance(init, dict):
            raise ValueError("'init' must be a string or an object")

        data_b64 = init.get("data")
        if data_b64:
            try:
                raw = base64.b64decode(data_b64)
            except Exception as exc:
                raise ValueError(f"'init.data' is not valid base64: {exc}") from exc
            if not raw:
                raise ValueError("'init.data' decoded to zero bytes")
            return Segment(url="", number=0, seg_type="init", inline_data=raw, size=len(raw))

        url = self._abs(init.get("url", ""))
        if not url:
            raise ValueError("'init' needs either 'url' or 'data'")

        return Segment(url=url, number=0, seg_type="init", byte_range=self._normalize_range(init.get("range")))

    @staticmethod
    def _normalize_range(value: Any) -> str:
        """Accept "a-b" or [a, b] and return the canonical "a-b" string ("" when absent)."""
        if value in (None, ""):
            return ""
        if isinstance(value, (list, tuple)):
            if len(value) != 2:
                raise ValueError(f"byte range {value!r} must have exactly 2 elements")
            return f"{int(value[0])}-{int(value[1])}"

        text = str(value).strip().replace("bytes=", "")
        if not re.fullmatch(r"\d+-\d+", text):
            raise ValueError(f"malformed byte range {value!r} (expected 'start-end')")
        return text

    def _build_media(self, spec: Any, stream: Stream, start_number: int) -> list[Segment]:
        if isinstance(spec, list):  # bare list is shorthand for {"list": [...]}
            spec = {"list": spec}
        if not isinstance(spec, dict):
            raise ValueError("'segments' must be an object or an array")

        if spec.get("list") is not None:
            segments = self._mode_list(spec["list"], start_number)
        elif spec.get("template"):
            segments = self._mode_template(spec, stream, start_number)
        elif spec.get("ranges") is not None or spec.get("chunk"):
            segments = self._mode_ranges(spec, start_number)
        else:
            raise ValueError("'segments' needs one of: 'list', 'template', 'ranges', 'chunk'")

        # Per-segment durations drive --max-time; fall back to an even split.
        if stream.duration > 0 and not any(seg.duration for seg in segments):
            even = stream.duration / len(segments)
            for seg in segments:
                seg.duration = even

        bitrate = stream.avg_bitrate or stream.bitrate
        if bitrate:
            for seg in segments:
                if not seg.size and seg.duration:
                    seg.estimated_size = int(bitrate / 8 * seg.duration)

        return segments

    def _mode_list(self, entries: Any, start_number: int) -> list[Segment]:
        if not isinstance(entries, list) or not entries:
            raise ValueError("'segments.list' must be a non-empty array")

        segments: list[Segment] = []
        for i, entry in enumerate(entries):
            if isinstance(entry, str):
                entry = {"url": entry}
            if not isinstance(entry, dict):
                raise ValueError(f"segment {i} is neither a string nor an object")

            url = self._abs(entry.get("url", ""))
            if not url:
                raise ValueError(f"segment {i} has no 'url'")

            segments.append(
                Segment(
                    url=url,
                    number=start_number + i,
                    seg_type="media",
                    size=int(entry.get("size") or 0),
                    duration=float(entry.get("duration") or 0.0),
                    byte_range=self._normalize_range(entry.get("range")),
                )
            )

        return segments

    def _mode_template(self, spec: dict[str, Any], stream: Stream, start_number: int) -> list[Segment]:
        template = str(spec["template"])
        is_time = "$Time" in template

        start = float(spec.get("start") or 0)
        step = float(spec.get("step") or spec.get("duration") or (0.0 if is_time else 1.0))
        count = spec.get("count")

        if is_time:
            if step <= 0:
                raise ValueError("a $Time$ template needs 'step' (or 'duration'): the segment length in seconds")

            if count is None:
                end = spec.get("end", stream.duration or 0)
                span = float(end) - start
                if span <= 0:
                    raise ValueError("a $Time$ template needs 'end' (or 'count', or a track 'duration')")
                count = math.ceil(span / step)
        else:
            if step <= 0:
                raise ValueError("'step' must be positive")

            if count is None:
                end = spec.get("end")
                if end is None:
                    raise ValueError("a $Number$ template needs 'end' or 'count'")
                count = math.floor((float(end) - start) / step) + 1

        count = int(count)
        if count <= 0:
            raise ValueError(f"template expands to {count} segments")

        seg_duration = step if is_time else float(spec.get("duration") or 0.0)
        segments: list[Segment] = []
        for i in range(count):
            position = start + i * step
            url = substitute(
                template,
                number=None if is_time else int(position),
                time_value=position if is_time else None,
                representation_id=stream.id,
            )
            segments.append(
                Segment(
                    url=self._abs(url),
                    number=start_number + i,
                    seg_type="media",
                    duration=seg_duration,
                )
            )

        logger.debug(
            f"CustomParser: template expanded to {count} segment(s) [{'time' if is_time else 'number'} mode, step={step}]"
        )
        return segments

    def _mode_ranges(self, spec: dict[str, Any], start_number: int) -> list[Segment]:
        url = self._abs(spec.get("url", ""))
        if not url:
            raise ValueError("byte-range segments need a 'url'")

        explicit = spec.get("ranges")
        if explicit:
            if not isinstance(explicit, list):
                raise ValueError("'segments.ranges' must be an array")
            pairs = [self._normalize_range(r) for r in explicit]
        else:
            chunk = int(spec.get("chunk") or 0)
            size = int(spec.get("size") or 0)
            if chunk <= 0 or size <= 0:
                raise ValueError("'chunk' splitting needs both 'chunk' and 'size' (bytes)")

            offset = int(spec.get("offset") or 0)
            pairs = []
            while offset < size:
                last = min(offset + chunk, size) - 1
                pairs.append(f"{offset}-{last}")
                offset = last + 1

        return [
            Segment(url=url, number=start_number + i, seg_type="media", byte_range=rng) for i, rng in enumerate(pairs)
        ]
