# 13.10.26

import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote

logger = logging.getLogger(__name__)

_AD_PATH_RE = re.compile(
    r"(?:^|/)(?:ad|ads|advert|dai|ssai|csai|impression|preroll|midroll|postroll)(?:[/?#]|$)",
    re.IGNORECASE,
)
_PLUTO_EXPLICIT_AD_RE = re.compile(r"ad_bumper|ad%2f|ad/creative", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?$"
)
_CUE_OUT_RE = re.compile(r"#EXT-X-CUE-OUT(?:-CONT)?:?(.*)$")
_CUE_IN_RE = re.compile(r"#EXT-X-CUE-IN")
_DATERANGE_RE = re.compile(r"#EXT-X-DATERANGE:([^\r\n]+)")
_MAP_URI_RE = re.compile(r'#EXT-X-MAP:[^\r\n]*URI="([^"]+)"')


def _parse_iso_duration(value: str | None) -> float:
    match = _DURATION_RE.match((value or "").strip())
    if not match:
        return 0.0
    years, months, days, hours, minutes, seconds = match.groups()
    return (
        int(years or 0) * 365 * 86400
        + int(months or 0) * 30 * 86400
        + int(days or 0) * 86400
        + int(hours or 0) * 3600
        + int(minutes or 0) * 60
        + float(seconds or 0)
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


_ID_TS_SUFFIX_RE = re.compile(r"^(.*)-(\d{10,})$")


def _period_id_prefix(period_id: str) -> str:
    """Group key for periods: Pluto appends a per-request timestamp to the id."""
    match = _ID_TS_SUFFIX_RE.match(period_id or "")
    return match.group(1) if match else (period_id or "")


def _period_ad_markers(period: ET.Element) -> list[str]:
    """URL/xlink/asset ad signals inside one MPD Period (Pluto stitcher tuned)."""
    markers: list[str] = []
    texts = [el.text or "" for el in period.iter() if _local(el.tag) == "BaseURL"]
    if any(_AD_PATH_RE.search(t) or _PLUTO_EXPLICIT_AD_RE.search(t) for t in texts):
        markers.append("ad-url")

    for attr_name, attr_value in period.attrib.items():
        if attr_name.lower().endswith("actuate") and (attr_value or "").strip().lower() == "onload":
            markers.append("xlink-onload")
            break

    for el in period.iter():
        if _local(el.tag) in ("AssetIdentifier", "EventStream"):
            blob = " ".join([el.text or ""] + [f"{k}={v}" for k, v in el.attrib.items()])
            if re.search(r"\bad(s|vert)?\b|preroll|midroll|postroll|\bDAI\b|\bSSAI\b", blob, re.I):
                markers.append("asset-ad")
                break
            
    return markers


def _decide_dash_periods(content: str) -> dict:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.debug(f"pluto ads: MPD unparseable, keeping everything: {e}")
        return {}

    periods = [el for el in root.iter() if _local(el.tag) == "Period"]
    if len(periods) < 2:
        return {}

    info = []
    for idx, period in enumerate(periods):
        has_video = has_audio = False
        langs: set[str] = set()
        for adapt in [el for el in period if _local(el.tag) == "AdaptationSet"]:
            mime = (adapt.get("contentType") or adapt.get("mimeType") or "").lower()
            kind = "video" if "video" in mime else "audio" if "audio" in mime else ""
            if not kind:
                codecs = ""
                for rep in adapt.iter():
                    if _local(rep.tag) == "Representation":
                        codecs = (rep.get("codecs") or "").lower()
                        break
                kind = "video" if codecs.startswith(("avc", "hvc", "hev", "vp", "av1")) else "audio" if codecs.startswith(("mp4a", "ac-", "ec-", "opus")) else ""
            if kind == "video":
                has_video = True
            elif kind == "audio":
                has_audio = True
                if adapt.get("lang"):
                    langs.add(adapt.get("lang").lower())
        
        info.append(
            {
                "idx": idx,
                "prefix": _period_id_prefix(period.get("id")),
                "duration": _parse_iso_duration(period.get("duration")),
                "has_video": has_video,
                "has_audio": has_audio,
                "langs": langs,
                "markers": _period_ad_markers(period),
            }
        )

    any_audio = any(p["has_audio"] for p in info)
    drop: list[int] = []
    reasons: list[str] = []
    totals: dict[str, float] = {}
    for p in info:
        totals[p["prefix"]] = totals.get(p["prefix"], 0.0) + p["duration"]

    if len(totals) > 1:
        dominant = max(totals, key=lambda k: totals[k])
        grand = sum(totals.values()) or 1.0
        if totals[dominant] > 0.5 * grand:
            for p in info:
                if p["prefix"] != dominant:
                    drop.append(p["idx"])
                    reasons.append(f"period {p['idx']} non-content id group")

    for p in info:
        if p["idx"] in drop:
            continue

        if p["has_video"] and not p["has_audio"] and any_audio:
            drop.append(p["idx"])
            reasons.append(f"period {p['idx']} video-only")
        elif p["markers"]:
            drop.append(p["idx"])
            reasons.append(f"period {p['idx']} ({','.join(p['markers'])})")

    # Safety: never drop everything, never drop the only periods with audio.
    if not drop or len(drop) >= len(info):
        return {}
    kept_audio = [p for p in info if p["idx"] not in drop and p["has_audio"]]
    if any_audio and not kept_audio:
        logger.info("pluto ads: drop would remove all audio periods, keeping everything")
        return {}

    logger.info(f"pluto ads: DASH drop periods {sorted(drop)} ({'; '.join(reasons)})")
    return {"drop_period_idx": sorted(drop), "reason": "; ".join(reasons)}


_HLS_AD_URL_RE = re.compile(r"ad_bumper|ad%2f|/creative/|47_ad|[_/]dai[_/]|[_/]ssai[_/]|[_/]ad[_/]", re.IGNORECASE)
_ISM_AD_ATTR_RE = re.compile(r"ad[_-]?bumper|preroll|midroll|postroll|/creative/|[_/]ads?[_/]|[_-]ads?[_-]", re.IGNORECASE)
_CLIP_ID_RE = re.compile(r"/clip/([^/_]+)")


def _hls_range_asset_key(url: str) -> str:
    """Coarse grouping key for discontinuity ranges: same asset = same group."""
    path = url.split("?", 1)[0]
    match = _CLIP_ID_RE.search(path)
    if match:
        return "clip:" + match.group(1)
    return "url:" + path.rsplit("/", 1)[0]


def _split_hls_ranges(content: str) -> list[dict]:
    """Split a child playlist into DISCONTINUITY ranges."""
    ranges: list[dict] = []
    current = {"seg_idx": [], "urls": [], "dur": 0.0, "cue_ad": False}
    in_ad = False
    idx = 0
    pending_dur = 0.0
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if _CUE_OUT_RE.match(line):
            in_ad = True
            continue

        if _CUE_IN_RE.match(line):
            in_ad = False
            continue

        if line.startswith("#EXT-X-DATERANGE:"):
            if re.search(r"SCTE35-OUT|CUE-OUT", line, re.I):
                in_ad = True
            elif re.search(r"SCTE35-IN|CUE-IN", line, re.I):
                in_ad = False
            continue

        if line.startswith("#EXT-X-DISCONTINUITY"):
            if current["seg_idx"]:
                ranges.append(current)
            current = {"seg_idx": [], "urls": [], "dur": 0.0, "cue_ad": False}
            continue
        if line.startswith("#EXTINF:"):
            try:
                pending_dur = float(line.split(":", 1)[1].split(",", 1)[0])
            except ValueError:
                pending_dur = 0.0
            continue

        if line and not line.startswith("#"):
            current["seg_idx"].append(idx)
            current["urls"].append(unquote(line))
            current["dur"] += pending_dur
            if in_ad:
                current["cue_ad"] = True
            idx += 1
            pending_dur = 0.0
        
    if current["seg_idx"]:
        ranges.append(current)
    return ranges


def _split_hls_blocks(content: str) -> list[dict]:
    """Split a child playlist into EXT-X-MAP blocks with ad-range flags."""
    blocks: list[dict] = []
    current: dict | None = None

    def _ensure(init_url: str | None) -> dict:
        nonlocal current
        current = {"init_url": init_url, "segments": [], "in_ad": False, "saw_ad": False}
        blocks.append(current)
        return current

    in_ad = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if _CUE_OUT_RE.match(line):
            in_ad = True
            continue

        if _CUE_IN_RE.match(line):
            in_ad = False
            continue

        if line.startswith("#EXT-X-DATERANGE:"):
            if re.search(r"SCTE35-OUT|CUE-OUT|AD", line, re.I):
                in_ad = True
            elif re.search(r"SCTE35-IN|CUE-IN", line, re.I):
                in_ad = False
            continue

        map_match = _MAP_URI_RE.match(line)
        if map_match:
            _ensure(map_match.group(1))
            continue

        if line.startswith("#EXTINF:"):
            if current is None:
                _ensure(None)
            current["segments"].append(line)
            if in_ad:
                current["saw_ad"] = True
            current["in_ad"] = current["in_ad"] or in_ad
    return blocks


def _decide_hls_ranges(content: str) -> dict:
    """Drop ad DISCONTINUITY ranges: explicit URL markers + non-dominant asset groups."""
    ranges = [r for r in _split_hls_ranges(content or "") if r["seg_idx"]]
    if len(ranges) < 2:
        return {}

    flagged = [any(_HLS_AD_URL_RE.search(u) for u in r["urls"]) or r["cue_ad"] for r in ranges]
    totals: dict[str, float] = {}
    for r, is_ad in zip(ranges, flagged, strict=True):
        if is_ad:
            continue
        key = _hls_range_asset_key(r["urls"][0]) if r["urls"] else "empty"
        totals[key] = totals.get(key, 0.0) + r["dur"]

    if len(totals) > 1:
        grand = sum(r["dur"] for r in ranges) or 1.0
        dominant = max(totals, key=lambda k: totals[k])
        if totals[dominant] > 0.5 * grand:
            flagged = [
                is_ad or (_hls_range_asset_key(r["urls"][0]) != dominant if r["urls"] else False)
                for r, is_ad in zip(ranges, flagged, strict=True)
            ]

    drop = sorted(i for r, is_ad in zip(ranges, flagged, strict=True) if is_ad for i in r["seg_idx"])
    total_segs = sum(len(r["seg_idx"]) for r in ranges)
    if not drop or len(drop) >= total_segs:
        return {}
    
    logger.info(f"pluto ads: HLS drop {len(drop)}/{total_segs} ad segment(s)")
    return {"drop_seg_idx": drop}


def _decide_hls_blocks(content: str, ctx: dict | None) -> dict:
    blocks = (ctx or {}).get("blocks")
    if blocks is None:
        parsed = _split_hls_blocks(content or "")
        blocks = [{"init_url": b["init_url"], "n_seg": len(b["segments"])} for b in parsed]
        ad_flags = [bool(b["saw_ad"]) for b in parsed]
    else:
        # Caller (core) split the blocks; re-derive ad ranges from content.
        parsed = _split_hls_blocks(content or "")
        ad_by_init: dict[str | None, bool] = {}
        for b in parsed:
            if b["segments"]:
                ad_by_init[b["init_url"]] = ad_by_init.get(b["init_url"], False) or bool(b["saw_ad"])
        ad_flags = [bool(ad_by_init.get(b.get("init_url"), False)) for b in blocks]

    decision: dict = {}
    if any(ad_flags):
        keep = list(dict.fromkeys(b["init_url"] for b, is_ad in zip(blocks, ad_flags, strict=True) if not is_ad and b.get("n_seg", 1)))
        if keep:
            dropped = sum(1 for f in ad_flags if f)
            logger.info(f"pluto ads: HLS drop {dropped} ad block(s), keep {len(keep)}")
            decision["keep_init_urls"] = keep
    decision.update(_decide_hls_ranges(content))
    return decision


def pluto_ad_decision(manifest_kind: str, content: str | None, ctx: dict | None = None) -> dict:
    """Decide which DASH periods / HLS segments / ISM chunks are stitcher ads."""
    try:
        if manifest_kind == "dash":
            if not content or "<Period" not in content:
                return {}
            return _decide_dash_periods(content)
        if manifest_kind == "hls":
            if not content or "#EXTINF" not in content:
                return {}
            return _decide_hls_blocks(content, ctx)
        if manifest_kind == "ism":
            if not content or "<StreamIndex" not in content:
                return {}
            return _decide_ism(content)
    except Exception as e:
        logger.debug(f"pluto ads: decision failed, keeping everything: {e}")
    return {}


def _decide_ism(content: str) -> dict:
    """Flag ad StreamIndex entries in a Smooth Streaming manifest."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        logger.debug(f"pluto ads: ISM unparseable, keeping everything: {e}")
        return {}

    indexes = list(root.iter("StreamIndex"))
    if len(indexes) < 2:
        return {}

    hits: list[str] = []
    for si in indexes:
        blob = " ".join([(si.get("Name") or ""), (si.get("Url") or "")])
        match = _ISM_AD_ATTR_RE.search(blob)
        if match:
            hits.append(match.group(0))

    hits = list(dict.fromkeys(hits))
    if not hits:
        return {}
    logger.info(f"pluto ads: ISM drop URL substrings {hits}")
    return {"drop_url_substrings": hits}
