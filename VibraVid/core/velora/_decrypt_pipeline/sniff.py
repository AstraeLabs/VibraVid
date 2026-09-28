# 01.04.25

from pathlib import Path

from VibraVid.core.ui.tracker import context_tracker


def _reads_as_self_initializing_mp4(path: Path) -> bool:
    """True if *path* starts with its own `ftyp` box — a complete, independently decryptable MP4 document — rather than a bare `moof`/`mdat` fragment meant to share a separate init segment."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return False
    return len(head) == 8 and head[4:8] == b"ftyp"


def _reads_as_plaintext_ts(path: Path) -> bool:
    """True if *path* already starts with the MPEG-TS sync byte (0x47) -- i.e. it is already decrypted (or was never encrypted), so decrypting it again would corrupt it."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(1)
    except OSError:
        return False
    return head == b"\x47"


def _skip_post_decrypt() -> bool:
    """Debug switch (--no-decrypt CLI flag): leave segments encrypted, no decrypt at all."""
    return context_tracker.skip_decrypt


def _streaming_mux_enabled() -> bool:
    """Fast path is on by default; --no-livemux CLI flag disables it for this run."""
    return not context_tracker.no_livemux
