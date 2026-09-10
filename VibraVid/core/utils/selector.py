# 13.03.26

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from VibraVid.core.utils.codec import DV_CODEC_PREFIXES, get_codec_token

logger = logging.getLogger(__name__)

_RES_TOKEN_RE = re.compile(r"^(\d+)[pP]?$")
_DV_SUFFIX_RE = re.compile(r"&dv(?:=([^&]*))?", re.IGNORECASE)


def strip_dv_suffix(video_filter: str) -> tuple[str, str | None]:
    """Strip a trailing '&dv' / '&dv=<quality>' companion tag from a video filter string."""
    vf = video_filter or ""
    m = _DV_SUFFIX_RE.search(vf)
    if not m:
        return vf.strip(), None
    quality = (m.group(1) or "worst").strip() or "worst"
    return (vf[: m.start()] + vf[m.end() :]).strip(), quality


def _height(s) -> int:
    h = getattr(s, "height", 0) or 0
    if h:
        return h
    res = getattr(s, "resolution", "") or ""
    try:
        return int(res.lower().replace("p", "").split("x")[-1])
    except (ValueError, IndexError):
        return 0


def _bitrate(s) -> int:
    return getattr(s, "bitrate", 0) or 0


def _language(s) -> str:
    return (getattr(s, "language", "") or "").strip().lower()


def _resolved_language(s) -> str:
    return (getattr(s, "resolved_language", "") or "").strip().lower()


def _codecs(s) -> str:
    return (getattr(s, "codecs", "") or "").strip().lower()


def _is_h265(s) -> bool:
    return _codecs(s).startswith(("hvc1", "hev1", "hevc", "h265"))


def _is_hdr10(s) -> bool:
    return (getattr(s, "video_range", "") or "").strip().lower() in {"hdr10", "hdr10+"}


def _is_encrypted(s) -> bool:
    drm = getattr(s, "drm", None)
    return bool(drm and drm.is_encrypted())


def _stream_id(s) -> str:
    sid = (getattr(s, "id", "") or "").strip()
    if not sid or sid in ("EXT",) or sid.startswith("vid:"):
        return ""
    return sid


@dataclass
class FilterSpec:
    """
    Parsed, structured representation of a user filter string.

    Accepted user-facing formats
    ----------------------------
    "best" / "worst"                        select best or worst stream
    "all"                                   select all streams
    "default"                               select only stream(s) with default=True
    "non-default"                           select only stream(s) with default=False
    "false"                                 drop (no download)
    "1080"                                  height constraint (video)
    "1920,H265"                             height + codec (video)
    ",H265"                                 codec only (video)
    "H265"                                  codec only bare (video)
    "1080|best"                             height with fallback to best (video)
    "1080|best,H265"                        height + codec with fallback to best (video)
    "ita|it" or "ita it"                    language tokens (audio/sub) — pipe or space separated
    "ita|it,AAC"                            language + codec (audio)
    ",AAC"                                  codec only (audio)
    "ita|best"                              language with fallback to best (audio)
    "ita|best,AAC"                          language + codec with fallback to best (audio)
    "r=1080:c=hvc1:f=best"                  native passthrough (resolution, codec, fallback)
    "i=audio_128k_en:f=best"                id-based (real manifest IDs).
    "b=1000-8000:f=best"                    bitrate range in kbps, best within range (video).
    "b=-8000:f=best"                        bitrate cap (no lower bound), best within range (video).

    Native `key=value` keys (short or long form, both accepted):
    r/res, l/lang/langs, c/codec/codecs, b/bitrate, i/id, f/for.
    E.g. "l=ita:c=aac:f=best" combines language + codec + fallback for audio.
    """

    drop: bool = False
    select_all: bool = False
    select_best: bool = True

    res: str | None = None
    langs: str | None = None
    codec: str | None = None
    id: str | None = None
    bitrate_min: int | None = None
    bitrate_max: int | None = None
    fallback_to_best: bool = False
    explicit_fallback: bool = False
    select_default: bool | None = None  # None=no filter, True=only default, False=only non-default
    extra: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str, stream_type: str) -> "FilterSpec":
        spec = cls()
        r = (raw or "").strip()

        if not r or r.lower() == "false":
            spec.drop = True
            return spec
        if r.lower() == "all":
            spec.select_all = True
            return spec
        if r.lower() == "best":
            return spec
        if r.lower() == "worst":
            spec.select_best = False
            return spec
        if r.lower() == "default":
            spec.select_default = True
            return spec
        if r.lower() == "non-default":
            spec.select_default = False
            return spec

        if "=" in r:
            spec._parse_native(r, stream_type)
            return spec

        # Split by comma to extract codec part if present
        parts = r.split(",", 1)
        primary = parts[0].strip()
        codec_s = parts[1].strip() if len(parts) > 1 else ""

        # Check for fallback strategy in codec part (e.g., "ac-3|best")
        if codec_s and "|" in codec_s:
            codec_parts = codec_s.split("|", 1)
            codec_part = codec_parts[0].strip()
            fallback_part = codec_parts[1].strip().lower()

            if fallback_part in ("best", "worst"):
                spec.explicit_fallback = True
                if fallback_part == "best":
                    spec.fallback_to_best = True
                elif fallback_part == "worst":
                    spec.fallback_to_best = False
                    spec.select_best = False

                codec_s = codec_part

        if codec_s:
            spec.codec = get_codec_token(codec_s, stream_type)

        if not primary:
            return spec

        # "best,H265" / "worst,H265" (also "best,AAC" for audio): explicit best/worst directive combined with a codec constraint.
        if primary.lower() in ("best", "worst"):
            spec.select_best = primary.lower() == "best"
            spec.explicit_fallback = True
            spec.fallback_to_best = spec.select_best
            return spec

        # Check for fallback strategy pattern (e.g., "1080|best", "ita|best")
        # Only trigger if primary contains "|" AND the second part is "best" or "worst"
        if "|" in primary:
            parts_pipe = primary.split("|", 1)
            constraint_part = parts_pipe[0].strip()
            fallback_part = parts_pipe[1].strip().lower()

            # Check if this is a fallback strategy pattern (second part is "best" or "worst")
            if fallback_part in ("best", "worst"):
                spec.explicit_fallback = True
                if fallback_part == "best":
                    spec.fallback_to_best = True
                elif fallback_part == "worst":
                    spec.fallback_to_best = False
                    spec.select_best = False

                # Now parse the constraint part (first part)
                res_m = _RES_TOKEN_RE.match(constraint_part)
                if res_m:
                    # Video height constraint: "1080|best" (also accepts "1080p|best")
                    spec.res = res_m.group(1)
                    return spec
                else:
                    # Language/audio constraint: "ita|best"
                    spec.langs = "|".join(t.strip() for t in re.split(r"[|\s]+", constraint_part) if t.strip())
                    return spec

        # Original logic for resolution constraint
        res_m = _RES_TOKEN_RE.match(primary)
        if res_m:
            spec.res = res_m.group(1)
            return spec

        # Original logic for codec-only patterns
        if not codec_s and "|" not in primary and " " not in primary:
            translated = get_codec_token(primary, stream_type)
            if translated.lower() != primary.lower():
                spec.codec = translated
                return spec

        # Language/subtitle tokens (including patterns like "ita|it", "ita it")
        raw_langs = primary
        spec.langs = "|".join(t.strip() for t in re.split(r"[|\s]+", raw_langs) if t.strip())
        return spec

    def _parse_native(self, r: str, stream_type: str) -> None:
        for_val = None
        for seg in r.split(":"):
            if "=" not in seg:
                continue
            k, v = seg.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k in ("r", "res"):
                self.res = v
            elif k in ("l", "lang", "langs"):
                self.langs = v
            elif k in ("c", "codec", "codecs"):
                self.codec = v
            elif k in ("i", "id"):
                self.id = v
            elif k in ("b", "bitrate"):
                self._parse_bitrate_range(v)
            elif k in ("f", "for"):
                for_val = v.lower()
            else:
                logger.warning(f"StreamSelector: unrecognized native filter key {k!r} in {r!r} -- stored under extra and NOT applied to selection")
                self.extra[k] = v

        if for_val == "all":
            self.select_all = True
            self.select_best = True
        elif for_val == "worst":
            self.select_best = False
            self.explicit_fallback = True
        elif for_val == "best":
            self.explicit_fallback = True

    def _parse_bitrate_range(self, v: str) -> None:
        """Parse a bitrate spec in kbps: 'MIN-MAX', '-MAX' (cap only), 'MIN-' (floor
        only), or a bare 'MAX' (cap only, e.g. "bitrate=8000" = no higher than 8000)."""
        v = v.strip()
        m = re.match(r"^(\d*)-(\d*)$", v)
        if m:
            lo, hi = m.group(1), m.group(2)
            self.bitrate_min = int(lo) if lo else None
            self.bitrate_max = int(hi) if hi else None
            return

        try:
            self.bitrate_min = None
            self.bitrate_max = int(v)
        except ValueError:
            self.bitrate_min = None
            self.bitrate_max = None


# ─────────────────────────────────────────────────────────────────────────────
# Selection result
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SelectionResult:
    """
    What StreamSelector found after progressive fallback.

    matched_res       Normalised HEIGHT string (not width). "1920" input on a 1920×1080 stream -> matched_res="1080".
    matched_langs     Pipe-separated ACTUAL stream language tags, e.g. "it-IT|en-US".
    matched_codec     Downloader codec token, e.g. "hvc1".
    matched_ids       Pipe-separated real manifest IDs (synthetic vid: excluded).
    extra             Passthrough key-values for the formatter (bwMin, bwMax, ...).
    """

    streams: list = field(default_factory=list)

    matched_res: str | None = None
    matched_langs: str | None = None
    matched_codec: str | None = None
    matched_ids: str | None = None
    extra: dict = field(default_factory=dict)

    drop: bool = False
    select_all: bool = False
    select_best: bool = True


class BaseFormatter(ABC):
    @abstractmethod
    def format(self, result: SelectionResult) -> str:
        """
        Return the CLI filter string for this track.

        Return values with special meaning (recognised by wrappers):
          "false"  -> drop / skip this track type
          "all"    -> pass all tracks of this type
          "best"   -> select the single best stream
          "worst"  -> select the single worst stream
        """


class StreamSelectorFormatter(BaseFormatter):
    """Converts a SelectionResult to a stream-selection argument string."""

    @staticmethod
    def _dedup_real_ids(matched_ids: str | None) -> str | None:
        """Strip synthetic 'vid:...' IDs and deduplicate."""
        if not matched_ids:
            return None
        seen: set = set()
        result: list[str] = []
        for i in matched_ids.split("|"):
            i = i.strip()
            if i and not i.startswith("vid:") and i not in seen:
                seen.add(i)
                result.append(i)
        return "|".join(result) if result else None

    @staticmethod
    def _bw_range(stream) -> str | None:
        """Return 'bwMin=N-10:bwMax=N+10' from stream.bitrate (kbps), or None."""
        bw = _bitrate(stream)
        if not bw:
            return None
        kbps = bw // 1000
        return f"bwMin={kbps - 10}:bwMax={kbps + 10}"

    @staticmethod
    def _stream_codecs(stream) -> str | None:
        """Return raw codecs string from stream, or None.
        For video streams, extract only the first codec (video codec, not audio).
        """
        c = (getattr(stream, "codecs", "") or "").strip()
        if not c:
            return None
        # For video streams, extract only the video codec (first element before comma)
        stream_type = getattr(stream, "type", "").lower()
        if stream_type == "video":
            first_codec = c.split(",")[0].strip()
            return first_codec if first_codec else None
        return c

    def format(self, result: SelectionResult) -> str:
        if result.drop:
            return "false"

        has_filters = bool(
            result.matched_ids or result.matched_res or result.matched_langs or result.matched_codec or result.extra
        )
        if result.select_all and not has_filters:
            return "all"
        if not has_filters:
            return "best" if result.select_best else "worst"

        real_ids = self._dedup_real_ids(result.matched_ids)

        if len(result.streams) > 1:
            return self._format_multi(result, real_ids)

        return self._format_single(result, real_ids)

    def _format_multi(self, result: SelectionResult, real_ids: str | None) -> str:
        """
        Multiple selected streams.

        Audio (multi-lang):
          • Has real IDs  -> id='\bA\b|\bB\b'  (no for=, downloader picks all matching)
          • No real IDs   -> lang='ita|it':codecs=...:for=allN
        """
        parts: list[str] = []

        if real_ids and not result.matched_res:
            id_tokens = [i.strip() for i in real_ids.split("|") if i.strip()]
            id_pattern = "|".join(rf"\b{i}\b" for i in id_tokens)
            parts.append(f"id='{id_pattern}'")
            for k, v in result.extra.items():
                parts.append(f"{k}={v}")

            parts.append("for=all")
            return ":".join(parts)

        # Fallback: language filter + optional codec + for=allN
        if result.matched_langs:
            raw_tokens = [t.strip().lower() for t in result.matched_langs.split("|") if t.strip()]
            seen_t: set = set()
            unique: list[str] = []
            for t in raw_tokens:
                if t not in seen_t:
                    seen_t.add(t)
                    unique.append(t)
            parts.append(f"lang='{('|'.join(unique))}'")
        if result.matched_codec:
            parts.append(f"codecs={result.matched_codec}")
        for k, v in result.extra.items():
            parts.append(f"{k}={v}")
        n = len(result.streams)
        parts.append(f"for=all{n}")
        return ":".join(parts)

    def _format_single(self, result: SelectionResult, real_ids: str | None) -> str:
        """
        Single selected stream (video or audio/subtitle).
        """
        parts: list[str] = []
        stream = result.streams[0] if result.streams else None

        if real_ids and not result.select_all:
            id_tokens = [i.strip() for i in real_ids.split("|") if i.strip()]
            if len(id_tokens) == 1:
                id_pattern = rf"\b{id_tokens[0]}\b"
                parts.append(f"id='{id_pattern}'")
                for k, v in result.extra.items():
                    parts.append(f"{k}={v}")
                parts.append(f"for={'best' if result.select_best else 'worst'}")
                return ":".join(parts)

        if result.matched_res:
            parts.append(f"res={result.matched_res}")
            if stream:
                raw_codec = self._stream_codecs(stream)
                if raw_codec:
                    parts.append(f"codecs={raw_codec}")
                bw = self._bw_range(stream)
                if bw:
                    parts.append(bw)
            elif result.matched_codec:
                parts.append(f"codecs={result.matched_codec}")
            for k, v in result.extra.items():
                parts.append(f"{k}={v}")
            parts.append(f"for={'best' if result.select_best else 'worst'}")
            return ":".join(parts)

        if result.matched_langs:
            raw_tokens = [t.strip().lower() for t in result.matched_langs.split("|") if t.strip()]
            seen_t: set = set()
            unique: list[str] = []
            for t in raw_tokens:
                if t not in seen_t:
                    seen_t.add(t)
                    unique.append(t)
            parts.append(f"lang='{('|'.join(unique))}'")
            if stream:
                raw_codec = self._stream_codecs(stream)
                if raw_codec:
                    parts.append(f"codecs={raw_codec}")
                bw = self._bw_range(stream)
                if bw:
                    parts.append(bw)
            elif result.matched_codec:
                parts.append(f"codecs={result.matched_codec}")
            for k, v in result.extra.items():
                parts.append(f"{k}={v}")
            parts.append(f"for={'best' if result.select_best else 'worst'}")
            return ":".join(parts)

        if result.matched_codec:
            parts.append(f"codecs={result.matched_codec}")
        for k, v in result.extra.items():
            parts.append(f"{k}={v}")
        if parts:
            parts.append(f"for={'best' if result.select_best else 'worst'}")
            return ":".join(parts)

        return "best" if result.select_best else "worst"


def _nearest_by_res(streams: list, res: str):
    """Stream whose height (or width, for portrait sources) is closest to *res*."""
    try:
        target = int(res)
    except (ValueError, TypeError):
        return None
    if not streams:
        return None

    def _distance(s):
        h = _height(s)
        w = getattr(s, "width", 0) or 0
        return min(abs(h - target), abs(w - target)) if (h or w) else abs(target)

    return min(streams, key=_distance)


def _matches_res(s, res: str) -> bool:
    try:
        target = int(res)
    except (ValueError, TypeError):
        return False
    h = _height(s)
    w = getattr(s, "width", 0) or 0
    return h == target or w == target


def _matches_bitrate(s, bmin: int | None, bmax: int | None) -> bool:
    """bmin/bmax are kbps; stream.bitrate is bps."""
    kbps = _bitrate(s) // 1000
    if not kbps:
        return False
    if bmin is not None and kbps < bmin:
        return False
    if bmax is not None and kbps > bmax:
        return False
    return True


def _matches_codec(s, token: str) -> bool:
    """Prefix/substring match; unknown codec treated as matching."""
    if not token:
        return True
    raw = _codecs(s)
    if not raw:
        return True
    return raw.startswith(token.lower()) or token.lower() in raw


def _matches_lang(s, langs: str) -> bool:
    """
    Match lang tokens against stream.language AND stream.resolved_language.

    Supports:
    - pipe-separated tokens:  "ita|it"
    - space-separated tokens: "ita it"  (treated identically)
    - ISO 639-2 three-letter codes (eng, ita, fra ...) matched against
      ISO 639-1 + region tags (en-US, it-IT, fr-FR ...) by trying the
      two-letter prefix.
    """
    tokens = [t.strip().lower() for t in re.split(r"[|\s]+", langs) if t.strip()]
    sl = _language(s)
    rl = _resolved_language(s)
    for t in tokens:
        if t == sl or t in sl:
            return True
        if rl and (t == rl or t in rl):
            return True

        if len(t) == 3 and t.isalpha():
            t2 = t[:2]
            if t2 == sl or t2 in sl:
                return True
            if rl and (t2 == rl or t2 in rl):
                return True
    return False


def _matches_id(s, id_pattern: str) -> bool:
    sid = _stream_id(s)
    if not sid:
        return False
    try:
        return bool(re.search(id_pattern, sid))
    except re.error:
        return sid == id_pattern


def _collect_ids(streams: list) -> str | None:
    """Deduplicated real manifest IDs (synthetic vid: excluded)."""
    seen: set = set()
    ids: list[str] = []
    for s in streams:
        sid = _stream_id(s)
        if sid and sid not in seen:
            seen.add(sid)
            ids.append(sid)
    return "|".join(ids) if ids else None


def _actual_langs(streams: list) -> str:
    """Return pipe-separated ACTUAL language tags from the selected streams, preserving insertion order and deduplicating."""
    seen: dict = {}
    for s in streams:
        lang = (getattr(s, "language", "") or "und").lower()
        seen[lang] = True
    return "|".join(seen.keys())


def _subtitle_pref_score(s) -> tuple:
    """Higher is better: prefer forced, then cleaner/non-accessibility variants."""
    return (
        1 if bool(getattr(s, "forced", False)) else 0,
        1 if not bool(getattr(s, "is_cc", False)) else 0,
        1 if not bool(getattr(s, "is_sdh", False)) else 0,
        1 if bool(getattr(s, "default", False)) else 0,
        _bitrate(s),
    )


def _subtitle_lang_key(s) -> str:
    """Key used for grouping subtitle variants of the same language."""
    return _resolved_language(s) or _language(s) or "und"


def _subtitle_flags(s) -> set:
    """Return a set of flags for a subtitle stream, e.g. {"forced", "cc"}."""
    flags: set = set()
    if bool(getattr(s, "forced", False)):
        flags.add("forced")
    if bool(getattr(s, "is_cc", False)):
        flags.add("cc")
    if bool(getattr(s, "is_sdh", False)):
        flags.add("sdh")
    return flags


def _canon_lang_token(token: str) -> str:
    """Canonicalise a language token for matching: lowercase, strip, remove region, etc."""
    t = (token or "").strip().lower().replace("_", "-")
    if not t:
        return ""
    base = t.split("-", 1)[0]
    if len(base) == 3 and base.isalpha():
        return base[:2]
    return base


def _parse_subtitle_lang_requests(langs: str) -> list[tuple[str, set]]:
    """Parse subtitle language requests into a list of (base_lang, flags) tuples."""
    requests: list[tuple[str, set]] = []
    seen: set = set()

    for raw in [t.strip().lower() for t in re.split(r"[|\s]+", langs or "") if t.strip()]:
        parts = [p for p in raw.split("_") if p]
        if not parts:
            continue

        base_raw = parts[0]
        flags = {p for p in parts[1:] if p in {"forced", "cc", "sdh", "hi"}}
        if "hi" in flags:
            flags.discard("hi")
            flags.add("cc")

        base = _canon_lang_token(base_raw)
        if not base:
            continue

        key = (base, tuple(sorted(flags)))
        if key in seen:
            continue
        seen.add(key)
        requests.append((base, flags))

    return requests


def _subtitle_matches_request(s, base: str, req_flags: set) -> bool:
    """Check if a subtitle stream matches the requested base language and flags."""
    if not _matches_lang(s, base):
        return False

    if not req_flags:
        return True

    sflags = _subtitle_flags(s)
    return req_flags.issubset(sflags)


def _subtitle_variant_key(s) -> tuple[str, ...]:
    """Variant bucket used for plain language requests (no explicit flags)."""
    flags = _subtitle_flags(s)
    ordered: list[str] = []
    for f in ("forced", "cc", "sdh"):
        if f in flags:
            ordered.append(f)
    return tuple(ordered)


def _normalize_filter_value(value, default: str) -> str:
    """Coerce a raw config/filter value to the string FilterSpec.parse() expects."""
    if value is None:
        return default
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class StreamSelector:
    def __init__(
        self,
        video_filter: str,
        audio_filter: str,
        subtitle_filter: str,
        formatter: BaseFormatter = None,
        prefer_h265: bool = False,
        prefer_hdr10: bool = False,
        prefer_drm: bool = False,
        require_drm: bool = False,
        minimum_video_height: int = 0,
    ):
        raw_vf = _normalize_filter_value(video_filter, "best").strip()
        self._vf, self._dv_quality = strip_dv_suffix(raw_vf)  # select_video in config.json
        self._af = _normalize_filter_value(audio_filter, "best").strip()
        self._sf = _normalize_filter_value(subtitle_filter, "all").strip()
        self._formatter = formatter or StreamSelectorFormatter()
        self._prefer_h265 = prefer_h265
        self._prefer_hdr10 = prefer_hdr10
        self._prefer_drm = prefer_drm
        self._require_drm = require_drm
        self._minimum_video_height = minimum_video_height

    def apply(self, streams: list) -> tuple[str, str, str]:
        """Mark stream.selected and return (sv, sa, ss) formatter strings."""
        pv = FilterSpec.parse(self._vf, "video")
        pa = FilterSpec.parse(self._af, "audio")
        ps = FilterSpec.parse(self._sf, "subtitle")

        rv = self._select_video(streams, pv)
        ra = self._select_audio(streams, pa)
        rs = self._select_subtitle(streams, ps)

        if self._dv_quality is not None:
            videos = [s for s in streams if getattr(s, "type", "") == "video"]
            target_res = rv.matched_res or pv.res
            self._mark_dv_companion(videos, self._dv_quality, target_res)

        sv = self._formatter.format(rv)
        sa = self._formatter.format(ra)
        ss = self._formatter.format(rs)

        logger.info(f"StreamSelector args -> video={sv!r}  audio={sa!r}  subtitle={ss!r}")
        return sv, sa, ss

    def _apply_default_filter(self, selected: list, spec: FilterSpec) -> list:
        """Filter selected streams by default flag if requested."""
        if spec.select_default is None:
            return selected
        if not selected:
            return selected

        filtered = [s for s in selected if bool(getattr(s, "default", False)) == spec.select_default]
        if filtered:
            logger.info(f"StreamSelector: applied default filter (select_default={spec.select_default}) -> {len(filtered)}/{len(selected)} streams")
            return filtered
        logger.info(f"StreamSelector: default filter (select_default={spec.select_default}) resulted in 0 streams, keeping original {len(selected)}")
        return selected

    def _select_video(self, streams: list, spec: FilterSpec) -> SelectionResult:
        result = SelectionResult(select_best=spec.select_best, extra=dict(spec.extra))
        videos = [s for s in streams if getattr(s, "type", "") == "video"]
        logger.debug(f"Video available: {[f'{_height(s)}p/{_codecs(s)}' for s in videos]} | filter: id={spec.id} res={spec.res} codec={spec.codec} bitrate=[{spec.bitrate_min},{spec.bitrate_max}] all={spec.select_all} drop={spec.drop} default={spec.select_default}")

        if spec.drop or not videos:
            result.drop = True
            return result

        if spec.bitrate_min is not None or spec.bitrate_max is not None:
            pool = [s for s in videos if _matches_bitrate(s, spec.bitrate_min, spec.bitrate_max)]
            if pool:
                videos = pool
            else:
                logger.info(f"StreamSelector video: bitrate=[{spec.bitrate_min},{spec.bitrate_max}] — no match, ignoring range")

        if self._minimum_video_height:
            eligible = [s for s in videos if _height(s) >= self._minimum_video_height]
            if eligible:
                videos = eligible
                logger.info(
                    f"StreamSelector video: minimum height {self._minimum_video_height}p "
                    f"-> {len(videos)} eligible stream(s)"
                )
            else:
                logger.warning(
                    f"StreamSelector video: no stream meets minimum height "
                    f"{self._minimum_video_height}p"
                )
                result.drop = True
                return result

        if self._require_drm:
            encrypted = [s for s in videos if _is_encrypted(s)]
            if not encrypted:
                logger.warning("StreamSelector video: no encrypted DRM stream available")
                result.drop = True
                return result
            videos = encrypted
            logger.info("StreamSelector video: requiring encrypted DRM streams")

        if self._prefer_drm and not spec.select_all:
            encrypted = [s for s in videos if _is_encrypted(s)]
            if encrypted:
                videos = encrypted
                logger.info("StreamSelector video: preferring encrypted DRM streams")

        # Handle explicit "default" or "non-default" filter
        if spec.select_default is not None and not spec.res and not spec.codec and not spec.id and not spec.select_all:
            filtered = [s for s in videos if bool(getattr(s, "default", False)) == spec.select_default]
            if filtered:
                s = (_best if spec.select_best else _worst)(filtered)
                if s:
                    s.selected = True
                    result.streams = [s]
                    logger.info(f"StreamSelector video: selected stream with default={spec.select_default}")
                return result
            logger.info(f"StreamSelector video: no stream with default={spec.select_default}")
            result.drop = True
            return result

        if spec.drop or not videos:
            result.drop = True
            return result

        if spec.select_all:
            for s in videos:
                s.selected = True
            result.streams = videos
            result.select_all = True
            return result

        if spec.id:
            pool = [s for s in videos if _matches_id(s, spec.id)]
            if pool:
                return self._mark_one_video(pool, _best if spec.select_best else _worst, result)
            logger.info(f"StreamSelector video: id={spec.id!r} — no match, relaxing")

        had_constraints = bool(spec.res or spec.codec)
        if spec.select_best and (self._prefer_h265 or self._prefer_hdr10):
            pick_exact = self._best_with_preferences
        else:
            pick_exact = _best if spec.select_best else _worst
        pick_fallback = _best if spec.fallback_to_best else _worst

        if spec.res and spec.codec:
            pool = [s for s in videos if _matches_res(s, spec.res) and _matches_codec(s, spec.codec)]
            if pool:
                return self._mark_one_video(pool, pick_exact, result, spec.res, spec.codec)
            logger.info(f"StreamSelector video: res={spec.res}+codec={spec.codec} — no match, relaxing")

        if spec.codec:
            pool = [s for s in videos if _matches_codec(s, spec.codec)]
            if pool:
                result.select_best = spec.fallback_to_best
                return self._mark_one_video(pool, pick_fallback, result, codec=spec.codec)
            logger.info(f"StreamSelector video: codec={spec.codec} — no match, relaxing")

        if spec.res:
            pool = [s for s in videos if _matches_res(s, spec.res)]
            if pool:
                if spec.codec:
                    result.select_best = spec.fallback_to_best
                    return self._mark_one_video(pool, pick_fallback, result, spec.res)
                return self._mark_one_video(pool, pick_exact, result, spec.res)
            logger.info(f"StreamSelector video: res={spec.res} — no match, falling back")

        if spec.res and not spec.explicit_fallback:
            s = _nearest_by_res(videos, spec.res)
            if s:
                logger.info(f"StreamSelector video: res={spec.res} — no exact match, using nearest available ({_height(s)}p)")
        elif had_constraints:
            result.select_best = spec.fallback_to_best
            s = pick_fallback(videos)
        else:
            s = pick_exact(videos)

        if s:
            s.selected = True
            result.streams = [s]
            actual_h = _height(s)
            if had_constraints and actual_h:
                result.matched_res = str(actual_h)
        return result

    def _best_with_preferences(self, streams: list):
        return max(
            streams,
            key=lambda stream: (
                int(self._prefer_hdr10 and _is_hdr10(stream)),
                int(self._prefer_h265 and _is_h265(stream)),
                _height(stream),
                _bitrate(stream),
            ),
        ) if streams else None

    def _select_audio(self, streams: list, spec: FilterSpec) -> SelectionResult:
        result = SelectionResult(select_best=spec.select_best, extra=dict(spec.extra))
        audios = [s for s in streams if getattr(s, "type", "") == "audio"]
        logger.debug(f"Audio available: {[f'{_language(s)}({_resolved_language(s)})/{_codecs(s)}' for s in audios]} | filter: id={spec.id} lang={spec.langs} codec={spec.codec} bitrate=[{spec.bitrate_min},{spec.bitrate_max}] all={spec.select_all} drop={spec.drop} default={spec.select_default}")

        if spec.drop or not audios:
            result.drop = True
            return result

        if spec.bitrate_min is not None or spec.bitrate_max is not None:
            pool = [s for s in audios if _matches_bitrate(s, spec.bitrate_min, spec.bitrate_max)]
            if pool:
                audios = pool
            else:
                logger.info(f"StreamSelector audio: bitrate=[{spec.bitrate_min},{spec.bitrate_max}] — no match, ignoring range")

        if self._prefer_drm and not spec.select_all:
            encrypted = [s for s in audios if _is_encrypted(s)]
            if encrypted:
                audios = encrypted
                logger.info("StreamSelector audio: preferring encrypted DRM streams")

        # Handle explicit "default" or "non-default" filter
        if (
            spec.select_default is not None
            and not spec.langs
            and not spec.codec
            and not spec.id
            and not spec.select_all
        ):
            filtered = [s for s in audios if bool(getattr(s, "default", False)) == spec.select_default]
            if filtered:
                self._mark_best_per_lang(filtered, spec.select_best)
                selected = [s for s in filtered if s.selected]
                result.streams = selected
                result.matched_ids = _collect_ids(selected)
                logger.info(f"StreamSelector audio: selected {len(selected)} stream(s) with default={spec.select_default}")
                return result
            logger.info(f"StreamSelector audio: no stream(s) with default={spec.select_default}")
            result.drop = True
            return result

        if spec.select_all and not spec.langs and not spec.codec and not spec.id:
            for s in audios:
                s.selected = True
            result.streams = audios
            result.select_all = True
            result.matched_ids = _collect_ids(audios)
            return result

        if spec.id:
            pool = [s for s in audios if _matches_id(s, spec.id)]
            if pool:
                self._mark_best_per_lang(pool, spec.select_best)
                selected = [s for s in pool if s.selected]
                result.streams = self._apply_default_filter(selected, spec)
                result.matched_ids = _collect_ids(result.streams)
                if spec.select_all:
                    result.select_all = True
                return result
            logger.info(f"StreamSelector audio: id={spec.id!r} — no match, relaxing")

        if spec.langs and spec.codec:
            pool = [s for s in audios if _matches_lang(s, spec.langs) and _matches_codec(s, spec.codec)]
            if pool:
                self._mark_best_per_lang(pool, spec.select_best)
                selected = [s for s in pool if s.selected]
                result.streams = self._apply_default_filter(selected, spec)
                result.matched_langs = _actual_langs(result.streams) or spec.langs
                result.matched_codec = spec.codec
                result.matched_ids = _collect_ids(result.streams)
                if spec.select_all:
                    result.select_all = True
                return result
            logger.info(f"StreamSelector audio: lang={spec.langs!r}+codec={spec.codec} — no match, relaxing")

        if spec.codec:
            pool = [s for s in audios if _matches_codec(s, spec.codec)]
            if pool:
                if spec.langs:
                    logger.info(f"StreamSelector audio: lang={spec.langs!r} not found with codec={spec.codec}")

                # Use fallback strategy when falling back to codec-only selection
                use_best_per_lang = (
                    spec.select_best if not (spec.langs and not spec.fallback_to_best) else spec.fallback_to_best
                )
                self._mark_best_per_lang(pool, use_best_per_lang)
                selected = [s for s in pool if s.selected]
                result.streams = self._apply_default_filter(selected, spec)
                result.matched_codec = spec.codec
                result.matched_ids = _collect_ids(result.streams)
                if spec.select_all:
                    result.select_all = True
                return result
            logger.info(f"StreamSelector audio: codec={spec.codec} not available — dropping")
            result.drop = True
            return result

        if spec.langs:
            pool = [s for s in audios if _matches_lang(s, spec.langs)]
            if pool:
                self._mark_best_per_lang(pool, spec.select_best)
                selected = [s for s in pool if s.selected]
                result.streams = self._apply_default_filter(selected, spec)
                result.matched_langs = _actual_langs(result.streams) or spec.langs
                result.matched_ids = _collect_ids(result.streams)
                if spec.select_all:
                    result.select_all = True
                return result
            logger.info(f"StreamSelector audio: lang={spec.langs!r} — no match, falling back to best available")
            self._mark_best_per_lang(audios, spec.select_best)
            selected = [s for s in audios if s.selected]
            result.streams = self._apply_default_filter(selected, spec)
            result.matched_ids = _collect_ids(result.streams)
            return result

        # No constraint: select best per lang across all available languages
        self._mark_best_per_lang(audios, spec.select_best)
        selected = [s for s in audios if s.selected]
        result.streams = self._apply_default_filter(selected, spec)
        result.matched_ids = _collect_ids(result.streams)
        return result

    def _select_subtitle(self, streams: list, spec: FilterSpec) -> SelectionResult:
        result = SelectionResult(select_best=spec.select_best, extra=dict(spec.extra))
        subs = [
            s
            for s in streams
            if getattr(s, "type", "") == "subtitle"
            and (getattr(s, "playlist_url", None) or getattr(s, "segments", None))
        ]
        logger.debug(f"Subtitle available: {[f'{_language(s)}({_resolved_language(s)})' for s in subs]} | filter: id={spec.id} lang={spec.langs} all={spec.select_all} drop={spec.drop} default={spec.select_default}")

        if spec.drop or not subs:
            result.drop = True
            return result

        # Handle explicit "default" or "non-default" filter
        if spec.select_default is not None and not spec.langs and not spec.id and not spec.select_all:
            filtered = [s for s in subs if bool(getattr(s, "default", False)) == spec.select_default]
            if filtered:
                self._mark_best_subtitle_per_lang(filtered)
                selected = [s for s in filtered if s.selected]
                result.streams = selected
                result.matched_ids = _collect_ids(selected)
                result.select_all = len(selected) > 1
                logger.info(f"StreamSelector subtitle: selected {len(selected)} stream(s) with default={spec.select_default}")
                return result
            logger.info(f"StreamSelector subtitle: no stream(s) with default={spec.select_default}")
            result.drop = True
            return result

        if spec.id:
            pool = [s for s in subs if _matches_id(s, spec.id)]
            if pool:
                for s in pool:
                    s.selected = True
                result.streams = self._apply_default_filter(pool, spec)
                result.matched_ids = _collect_ids(result.streams)
                result.select_all = True
                return result
            logger.info(f"StreamSelector subtitle: id={spec.id!r} — no match, relaxing")

        if spec.select_all and not spec.langs:
            for s in subs:
                s.selected = True
            result.streams = self._apply_default_filter(subs, spec)
            result.select_all = True
            return result

        if spec.langs:
            requests = _parse_subtitle_lang_requests(spec.langs)
            selected: list = []
            used_ids: set = set()

            for base, req_flags in requests:
                pool = [s for s in subs if _subtitle_matches_request(s, base, req_flags)]
                if not pool:
                    continue

                # Explicit flags (e.g. it_forced) are strict: choose one best matching track.
                if req_flags:
                    pick = sorted(pool, key=_subtitle_pref_score, reverse=True)[0]
                    sid = _stream_id(pick) or id(pick)
                    if sid in used_ids:
                        continue
                    used_ids.add(sid)
                    pick.selected = True
                    selected.append(pick)
                    continue

                # Plain language request (e.g. "it"): keep one best track per subtitle variant
                # so we can download CC + Forced + SDH + Plain without duplicate copies.
                variants: dict = {}
                for s in pool:
                    vkey = _subtitle_variant_key(s)
                    variants.setdefault(vkey, []).append(s)

                for _vkey, vpool in variants.items():
                    pick = sorted(vpool, key=_subtitle_pref_score, reverse=True)[0]
                    sid = _stream_id(pick) or id(pick)
                    if sid in used_ids:
                        continue
                    used_ids.add(sid)
                    pick.selected = True
                    selected.append(pick)

            if selected:
                result.streams = self._apply_default_filter(selected, spec)
                result.matched_langs = _actual_langs(result.streams) or spec.langs
                result.matched_ids = _collect_ids(result.streams)
                result.select_all = len(result.streams) > 1
                return result

            logger.info(f"StreamSelector subtitle: lang={spec.langs!r} — no match, dropping")
            result.drop = True
            return result

        for s in subs:
            s.selected = True
        result.streams = self._apply_default_filter(subs, spec)
        result.select_all = True
        return result

    # ── Internal helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _mark_one_video(
        pool: list, pick_fn, result: SelectionResult, res: str | None = None, codec: str | None = None
    ) -> SelectionResult:
        """Pick one video stream and populate result."""
        s = pick_fn(pool)
        if s:
            s.selected = True
            result.streams = [s]
            result.matched_codec = codec
            if res is not None:
                actual_h = _height(s)
                result.matched_res = str(actual_h) if actual_h else res
            real_id = _stream_id(s)
            if real_id:
                result.matched_ids = real_id
        return result

    @staticmethod
    def _mark_best_per_lang(streams: list, best: bool = True) -> None:
        seen: dict = {}
        for s in sorted(streams, key=_bitrate, reverse=best):
            lang = _language(s) or "und"
            if lang not in seen:
                seen[lang] = True
                s.selected = True

    @staticmethod
    def _mark_best_subtitle_per_lang(streams: list) -> None:
        seen: dict = {}
        for s in sorted(streams, key=_subtitle_pref_score, reverse=True):
            lang = _subtitle_lang_key(s)
            if lang not in seen:
                seen[lang] = True
                s.selected = True

    def _mark_dv_companion(self, video_streams: list, quality: str, target_res: str | None = None) -> None:
        """Find the DV companion stream and mark it with dv_companion=True (not selected)."""

        def _is_dv(s) -> bool:
            codecs = (getattr(s, "codecs", "") or "").lower()
            if any(codecs.startswith(p) for p in DV_CODEC_PREFIXES):
                return True
            return (getattr(s, "video_range", "") or "").upper() == "DV"

        dv_streams = [s for s in video_streams if _is_dv(s) and not getattr(s, "selected", False)]
        if not dv_streams:
            logger.info("StreamSelector &dv: no unselected DV streams found")
            return

        q = (quality or "worst").strip().lower()
        is_explicit_height = bool(re.match(r"^\d+$", q))

        pool = dv_streams
        if target_res and not is_explicit_height:
            res_pool = [s for s in dv_streams if _matches_res(s, target_res)]
            if res_pool:
                pool = res_pool
            else:
                nearest = _nearest_by_res(dv_streams, target_res)
                if nearest:
                    nearest.dv_companion = True
                    nearest.dv_companion_quality = quality
                    logger.info(f"StreamSelector &dv: no DV stream at {target_res}p, using nearest available ({_height(nearest)}p/{_codecs(nearest)})")
                    return

        sortable = [s for s in pool if _bitrate(s)]
        pool = sortable or pool

        if is_explicit_height:
            target_h = int(q)
            companion = min(pool, key=lambda s: abs(_height(s) - target_h))
        elif q == "best":
            companion = max(pool, key=_bitrate)
        else:
            companion = min(pool, key=_bitrate)

        companion.dv_companion = True
        companion.dv_companion_quality = quality
        logger.info(f"StreamSelector &dv: marked companion {_height(companion)}p/{_codecs(companion)} (quality={quality!r})")

def _best(streams: list):
    return max(streams, key=lambda stream: (_height(stream), _bitrate(stream))) if streams else None


def _worst(streams: list):
    return min(streams, key=_bitrate) if streams else None
