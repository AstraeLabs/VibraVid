# 13.03.26


from rich.table import Table
from rich.text import Text

from VibraVid.core.manifest.stream import Stream as _Stream
from VibraVid.core.utils.codec import get_channel_label
from VibraVid.utils.console.shared_styles import TableStyle, create_styled_table

_COL_VIDEO = "cyan"
_COL_AUDIO = "green"
_COL_SUB = "yellow"
_COL_DRM = "red"
_COL_DRM_MULTI = "bold red"
_COL_BITRATE = "bright_blue"
_COL_LANG = "bright_magenta"
_COL_CODEC = "bright_cyan"
_COL_RES = "bright_green"
_COL_HDR = "bold yellow"
_TYPE_COLOUR = {"video": _COL_VIDEO, "audio": _COL_AUDIO, "subtitle": _COL_SUB}

_HDR_STYLE = {
    "HDR10": "bold yellow",
    "HDR10+": "bold yellow",
    "HLG": "bold green",
    "DV": "bold magenta",
    "PQ": "bold yellow",
    "HDR": "bold yellow",
}

TABLE_STYLE = TableStyle.MODERN_ROUNDED


def _c(text: str, colour: str | None) -> Text:
    return Text(str(text), style=colour) if colour else Text(str(text))


def sort_streams_key(s):
    """Sort key for stream display: video first, then audio, then subtitle."""
    is_ext = getattr(s, "is_external", False) or getattr(s, "id", "") == "EXT"
    stype = getattr(s, "type", "")
    order = {"video": 0, "audio": 1, "subtitle": 2}.get(stype, 3)
    bitrate = getattr(s, "bitrate", 0) or 0
    return (order, int(is_ext), -bitrate)


def build_table(
    streams: list,
    selected: set[int] | None = None,
    cursor: int | None = None,
    window_size: int = 15,
    highlight_cursor: bool = True,
) -> Table:
    """
    Build and return a Rich stream-selection table.

    Display order: Video (bitrate desc) -> Audio (bitrate desc) -> Subtitle.
    External streams (*EXT) appear at the end of their category.
    """
    table = create_styled_table(TABLE_STYLE)

    cols = [
        ("#", "right"),
        ("Type", "left"),
        ("DRM", "center"),
        ("Sel", "center"),
        ("Resolution", "left"),
        ("Bitrate", "right"),
        ("Codec", "left"),
        ("Channels", "center"),
        ("Language", "left"),
        ("Extra", "center"),
    ]
    for name, justify in cols:
        table.add_column(name, justify=justify, no_wrap=True)

    sorted_streams = sorted(streams, key=sort_streams_key)
    total = len(sorted_streams)

    interactive = cursor is not None
    if interactive:
        half = max(1, window_size // 2)
        start = max(0, cursor - half)
        end = min(total, start + window_size)
        if end - start < window_size:
            start = max(0, end - window_size)
    else:
        start, end = 0, total

    _ellipsis = ("...", "", "", "", "", "", "", "", "", "", "")
    if interactive and start > 0:
        table.add_row(*_ellipsis)

    for orig_idx, s in enumerate(sorted_streams):
        if not (start <= orig_idx < end):
            continue

        is_ext = getattr(s, "is_external", False) or getattr(s, "id", "") == "EXT"
        stype_raw = getattr(s, "type", "")
        type_col = _TYPE_COLOUR.get(stype_raw, "white")

        if isinstance(s, _Stream):
            stype_label = s.get_type_display()

            is_sel = s.selected if not interactive else (orig_idx in (selected or set()))
            res = s.resolution if s.type == "video" else ""
            hdr = (s.get_hdr_display() if s.type == "video" else "") or s.name
            bitrate = s.bitrate_display if s.bitrate else ""
            codec = s.get_short_codec()
            channels = get_channel_label(s.channels) if s.channels else ""
            language = s.language if s.language not in ("und", "") else ""
            drm = s.drm.get_drm_display() if s.drm and (s.drm.is_encrypted() or s.drm.get_all_drm_types()) else ""

        else:
            # Legacy StreamInfo compatibility
            stype_label = getattr(s, "type", "")
            if is_ext and "*EXT" not in stype_label:
                stype_label = f"{stype_label} *EXT"
            is_sel = orig_idx in (selected or set()) if interactive else getattr(s, "selected", False)
            res = getattr(s, "resolution", "") if stype_raw.lower() == "video" else ""
            hdr = ""
            bw = getattr(s, "bandwidth", "") or ""
            bitrate = "" if bw in ("0 bps", "N/A") else bw
            codec = s.get_short_codec() if hasattr(s, "get_short_codec") else ""
            channels = get_channel_label(getattr(s, "channels", "") or "")
            language = getattr(s, "language", "") or ""
            drm = ""

        # ── Row style ─────────────────────────────────────────────────────────
        if interactive and highlight_cursor and orig_idx == cursor:
            row_style = "bold white on blue"
        else:
            row_style = None

        sel_text = _c("X", "bold bright_green") if is_sel else _c("", "")
        drm_col = _COL_DRM_MULTI if "+" in drm else _COL_DRM
        hdr_col = _HDR_STYLE.get(hdr.upper(), _COL_HDR) if hdr else None

        table.add_row(
            _c(str(orig_idx + 1), None),
            _c(stype_label, type_col),
            _c(drm, drm_col if drm else None),
            sel_text,
            _c(res, _COL_RES if res else None),
            _c(bitrate, _COL_BITRATE if bitrate else None),
            _c(codec, _COL_CODEC if codec else None),
            _c(channels, "white" if channels else None),
            _c(language, _COL_LANG if language else None),
            _c(hdr, hdr_col),
            style=row_style,
        )

    if interactive and end < total:
        table.add_row(*_ellipsis)

    return table
