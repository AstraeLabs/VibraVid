# 01.04.25

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from VibraVid.core.ui.bar_manager import DownloadBarManager

if TYPE_CHECKING:
    from VibraVid.core.decryptor import Decryptor
    from VibraVid.core.muxing.streaming_mux import StreamingMuxFeeder

    from .live_merge import _LiveMerger


@dataclass
class _SegmentDownloadContext:
    """Per-call state shared across one _download_stream_generic() invocation."""
    stream: Any
    protocol: str
    protocol_lower: str
    bar_manager: DownloadBarManager
    task_key: str
    dl_segs: list[dict]
    total: int
    stream_dir: Path
    all_headers: dict
    progress_label: str
    default_ext: str = ""
    live_decryption: bool = False

    # Callbacks bound to this context (stop request / progress bar / velora events)
    stop: Callable[[], bool] | None = None
    progress_cb: Callable[..., None] | None = None
    event_cb: Callable[[dict], None] | None = None

    # Segment bookkeeping
    key_cache: dict[str, bytes] = field(default_factory=dict)
    key_cache_lock: threading.Lock = field(default_factory=threading.Lock)
    segment_meta_by_path: dict = field(default_factory=dict)
    no_dedicated_init: bool = False
    media_segs_only: list[dict] = field(default_factory=list)
    total_duration: float = 0.0
    seg_dur_cumulative: list[float] = field(default_factory=list)
    expected_live_order: list = field(default_factory=list)

    # Decrypt worker coordination
    decrypt_queue: "queue.Queue" = field(default_factory=queue.Queue)
    decrypt_errors: list[str] = field(default_factory=list)
    seg_errors: list[str] = field(default_factory=list)
    decrypt_aborted_reason: str | None = None
    decrypt_threads: list[threading.Thread] = field(default_factory=list)
    key_sanity_checked: bool = False

    # Live merge / streaming mux
    live_merger: "_LiveMerger | None" = None
    live_out_path: Path | None = None
    dash_init: Path | None = None
    dash_init_lock: threading.Lock = field(default_factory=threading.Lock)
    dash_pending: list[tuple] = field(default_factory=list)
    ism_init: "tuple[Path, Path] | None" = None
    ism_init_lock: threading.Lock = field(default_factory=threading.Lock)
    dash_decryptor: "Decryptor | None" = None
    ism_decryptor: "Decryptor | None" = None
    streaming_feeder: "StreamingMuxFeeder | None" = None
    mux_setup_thread: threading.Thread | None = None
    feeder_box: list = field(default_factory=lambda: [None])
    mux_join_timeout: float = 0.0

    # Progress reporting
    first_bytes_logged: bool = False
    last_total_bytes: int = 0

    # needs_* dispatch flags (computed once, read by the worker dispatch + finalize)
    needs_hls_decrypt: bool = False
    needs_dash_live: bool = False
    needs_ism_live: bool = False
    needs_hls_live: bool = False
    needs_dash_clear_merge: bool = False
    needs_ism_clear_merge: bool = False
    needs_hls_clear_merge: bool = False
