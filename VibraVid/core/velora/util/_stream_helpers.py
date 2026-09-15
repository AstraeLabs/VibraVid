# 10.04.26


import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from rich.console import Console

from VibraVid.core.decryptor import KeysManager
from VibraVid.core.ui.bar_manager import DownloadBarManager

console = Console()
logger = logging.getLogger("manual")

_SEGMENT_EXTENSIONS = (
    "mp4",
    "m4s",
    "m4v",
    "m4a",
    "m4i",
    "m4f",  # ISO-BMFF / fragmented-MP4 (incl. EXT-X-MAP init .m4i)
    "cmfv",
    "cmfa",
    "cmft",
    "cmfs",  # CMAF
    "m2ts",
    "ts",  # MPEG-TS
    "aac",
    "ac3",
    "ec3",
    "mp3",
    "mov",
    "webm",  # other media containers
    "vtt",
    "srt",
    "ttml",
    "dfxp",
    "ass",
    "ssa",  # subtitles
)
_FMP4_MERGED_AS_MP4 = frozenset({"m4s", "m4v", "m4i", "m4f", "cmfv"})
_SUBTITLE_EXTENSIONS = (
    "webvtt",
    "vtt",
    "srt",
    "ass",
    "ssa",
    "ttml2",
    "ttml",
    "xml",
    "dfxp",
    "m4a",
    "aac",
    "mp3",
)
_SUBTITLE_EXT_NORMALISED = {"webvtt": "vtt"}
_REDIRECT_URL_RE = re.compile(r"""https?:\\?/\\?/[^\s"'<>]+""")
_FRAGMENT_LEADING_BOX_TYPES = frozenset({b"moof", b"mdat", b"styp", b"sidx", b"free", b"skip", b"emsg", b"prft"})
_RANGE_HEADER_RE = re.compile(r"bytes=(\d+)-(\d+)")


def _ext_from_url_canon(url: str, extensions: tuple, default: str = "") -> str:
    """Single URL→extension detector shared by all callers."""
    path = (url or "").split("?")[0].lower()
    for ext in extensions:
        if path.endswith(f".{ext}"):
            return _SUBTITLE_EXT_NORMALISED.get(ext, ext)
    return default


def detect_seg_ext(url: str, default: str = "ts") -> str:
    """Detect the media-segment container format from a URL path."""
    return _ext_from_url_canon(url, _SEGMENT_EXTENSIONS, default=default)


def is_valid_frag_init(data: bytes) -> bool:
    """True if *data* is a real ftyp+moov init segment (optionally +sidx) -- the same shape live per-fragment decrypt (`decrypt_segment_live`, `--fragments-info`) needs to work."""
    off = 0
    seen_ftyp = False
    seen_moov = False
    n = len(data)
    
    while off + 8 <= n:
        size = int.from_bytes(data[off : off + 4], "big")
        typ = data[off + 4 : off + 8]
        hdr = 8
        if size == 1:
            if off + 16 > n:
                break
            size = int.from_bytes(data[off + 8 : off + 16], "big")
            hdr = 16
        elif size == 0:
            size = n - off
        if size < hdr:
            break
        if typ == b"ftyp":
            seen_ftyp = True
        elif typ == b"moov":
            seen_moov = True
        elif typ in (b"moof", b"mdat"):
            # A real init never carries sample data itself.
            return False
        if seen_ftyp and seen_moov:
            return True
        off += size
    return seen_ftyp and seen_moov


def extract_redirect_url(data: bytes) -> str | None:
    """Best-effort extraction of a real media URL from a response body that isn't a valid init segment."""
    if len(data) > 8192:
        return None
    try:
        text = data.decode("utf-8", errors="ignore")
    except Exception:
        return None
    m = _REDIRECT_URL_RE.search(text)
    if not m:
        return None
    url = m.group(0).replace("\\/", "/").replace("\\u0026", "&")
    return url or None


def repair_init_segment(data: bytes) -> str | None:
    """If *data* is not a valid ftyp+moov init segment (see `is_valid_frag_init`), return a candidate real URL parsed out of the body for the caller to re-fetch (see `extract_redirect_url`)."""
    if is_valid_frag_init(data):
        return None
    return extract_redirect_url(data)


def parse_range_header(range_header: str | None) -> tuple[int, int] | None:
    """Parse a `Range: bytes=start-end` header (the single-range form VibraVid emits internally for byte-range DASH SegmentList addressing) into an inclusive ``(start, end)`` int pair."""
    if not range_header:
        return None
    m = _RANGE_HEADER_RE.fullmatch(range_header.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def looks_like_bare_fragment(data: bytes) -> bool:
    """True if *data* starts with a top-level box type a bare moof+mdat
    media fragment. False for a self-initializing document
    (ftyp-first -- a whole standalone MP4 instead of just the requested
    byte range, e.g. when a CDN redirect drops the Range header) or
    anything that isn't recognizable ISOBMFF at all."""
    if len(data) < 8:
        return False
    return data[4:8] in _FRAGMENT_LEADING_BOX_TYPES


def merged_segment_ext(sample_url: str, default: str = "ts") -> str:
    """Container extension for a merged (concatenated) segment file."""
    ext = detect_seg_ext(sample_url, default=default)
    return "mp4" if ext in _FMP4_MERGED_AS_MP4 else ext


def safe_name(s: str, maxlen: int = 32) -> str:
    """Sanitise *s* for use as a file/directory name component."""
    cleaned = re.sub(r"[^\w\-]", "_", s or "").strip("_")
    return (cleaned or "x")[:maxlen]


def describe_key_for_log(value: Any) -> str:
    """Return a safe, non-sensitive textual description of a decryption key value."""
    if value is None:
        return "none"
    if isinstance(value, KeysManager):
        try:
            return f"KeysManager(len={len(value.get_keys_list())})"
        except Exception:
            return "KeysManager"
    if isinstance(value, str):
        return f"str(len={len(value)})"
    if isinstance(value, (bytes, bytearray)):
        return f"{type(value).__name__}(len={len(value)})"
    if isinstance(value, (list, tuple, set)):
        return f"{type(value).__name__}(len={len(value)})"
    return type(value).__name__


def join_interruptible(
    threads: list[threading.Thread], stop_event: threading.Event, poll: float = 0.25, hard_timeout: float = 7200.0
) -> None:
    """
    Join *threads* in a polling loop so ``KeyboardInterrupt`` is always
    deliverable (unlike a plain ``thread.join()`` with a long timeout).

    The loop exits as soon as all threads finish, *stop_event* is set, or
    *hard_timeout* seconds elapse — whichever comes first.
    """
    deadline = time.monotonic() + hard_timeout
    while True:
        alive = [t for t in threads if t.is_alive()]
        if not alive:
            break
        if stop_event.is_set() or time.monotonic() >= deadline:
            break
        for t in alive:
            t.join(timeout=poll)


def collect_failed_segments(dl_segs: list, downloaded_paths: list, stream_dir, default_ext: str) -> list:
    """
    Return a list of (seg_number, url) tuples for segments that were not
    successfully downloaded (missing file or zero-byte file).
    """
    downloaded_set = {
        str(p.resolve()).casefold() for p in (downloaded_paths or []) if p.exists() and p.stat().st_size > 0
    }

    failed = []
    for seg in dl_segs:
        seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
        if seg_ext == "m4s":
            seg_ext = "mp4"

        expected_path = Path(stream_dir) / f"seg_{seg['number']:05d}.{seg_ext}"
        key = str(expected_path.resolve()).casefold()
        if key not in downloaded_set:
            failed.append((seg["number"], seg.get("url", "N/A")))

    return failed


def print_failed_segments_report(failed_by_stream: list) -> None:
    """Print a summary of all failed segments after all progress bars are gone."""
    if not failed_by_stream:
        return

    console.print()
    for stream_label, failed in failed_by_stream:
        if not failed:
            continue

        logger.error(f"Failed segments for {stream_label!r}: {len(failed)} missing")
        console.print(f"[bold red]Dio Cancaro:[/bold red] [bold white]{stream_label}[/bold white] [red]({len(failed)} missing)[/red]")


class SilentDownloadBarManager(DownloadBarManager):
    """
    A no-op drop-in for ``DownloadBarManager`` that skips all Rich
    Live/Progress setup.  Used when ``show_progress=False`` is passed to
    ``MediaDownloader.start_download()``.
    """

    def __init__(self, download_id: str | None = None) -> None:
        # Intentionally skip super().__init__() — we do not want Rich objects.
        self.download_id = download_id
        self.progress = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def add_prebuilt_tasks(self, prebuilt_tasks):
        return None

    def add_external_track_task(self, label, track_key):
        return None

    def handle_progress_line(self, parsed):
        return None

    def finish_all_tasks(self):
        return None
