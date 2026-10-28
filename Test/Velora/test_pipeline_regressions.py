import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibraVid.core.velora._decrypt_pipeline.dash import DashDecryptMixin
from VibraVid.core.velora._decrypt_pipeline.worker import WorkerMixin

REPO_ROOT = Path(__file__).resolve().parents[2]
FTYP = (24).to_bytes(4, "big") + b"ftyp" + b"isom" + b"\0" * 12

# Undefined names are runtime NameErrors. Inside the chunks pipe they were swallowed by a broad `except Exception`
# and silently redid the whole decrypt + mux: three of them shipped in a row and no test noticed.
CHECKED_DIRS = [
    "VibraVid/core/velora/_decrypt_pipeline",
    "VibraVid/core/muxing",
    "VibraVid/core/decryptor",
    "VibraVid/core/downloader",
]


def test_decrypt_and_mux_modules_have_no_undefined_or_shadowed_names():
    if shutil.which("ruff") is None:
        pytest.importorskip("ruff")
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--isolated", "--no-cache", "--select", "F821,F822,F823,F811,F841", *CHECKED_DIRS],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- whole-file adopt keeps seg_NNNNN as a link to the track output ---------------------------------------------


class _Merger:
    def __init__(self, out_path: Path):
        self.out_path = out_path

    def can_adopt(self) -> bool:
        return True

    def adopt(self, dec_tmp: Path) -> bool:
        dec_tmp.replace(self.out_path)
        return True


class _Decryptor:
    def decrypt_segment_live(self, encrypted_path, decrypted_path, **kwargs):
        Path(decrypted_path).write_bytes(b"decrypted")
        return True, None, None


def test_adopted_segment_is_replaced_by_a_link_to_the_output(tmp_path):
    seg = tmp_path / "seg_00000.mp4"
    seg.write_bytes(FTYP + b"encrypted")
    out = tmp_path / "video.mp4"

    host = object.__new__(DashDecryptMixin)
    host.key = "kid:key"
    host._repair_media_segment = lambda *a, **k: None
    host._want_key_sanity = lambda *a, **k: False
    ctx = SimpleNamespace(
        protocol="dash",
        live_merger=_Merger(out),
        media_segs_only=[{"number": 0}],
        stream=SimpleNamespace(),
        key_sanity_checked=False,
    )

    host._decrypt_dash_segment(ctx, seg, {"number": 0, "seg_type": "media"}, _Decryptor(), None)

    # The missing-segment census looks for seg_NNNNN: it must still exist and carry the decrypted bytes (a link, or a copy where the
    # filesystem has no hard links), never the old encrypted ones and never nothing.
    assert out.read_bytes() == b"decrypted"
    assert seg.read_bytes() == b"decrypted"


# --- one wrong-key skip per track, not one per queued segment -----------------------------------------------------


def _worker_ctx(segments: int) -> SimpleNamespace:
    decrypt_queue: queue.Queue = queue.Queue()
    for _ in range(segments):
        decrypt_queue.put({"path": "unused"})
    decrypt_queue.put(None)
    return SimpleNamespace(
        decrypt_queue=decrypt_queue,
        decrypt_stop=threading.Event(),
        decrypt_lock=threading.Lock(),
        decrypt_aborted_reason=None,
        stream=SimpleNamespace(),
        bar_manager=object(),
        task_key="vid",
    )


def test_poisoned_kid_skips_the_track_once(monkeypatch):
    host = object.__new__(WorkerMixin)
    skipped = []
    host._stream_kids = lambda stream: {"kid"}
    host._kids_poisoned = lambda kids: "kid"
    host._skip_stream_wrong_key = lambda *a, **k: skipped.append(a)
    ctx = _worker_ctx(segments=25)

    host._decrypt_worker(ctx, live_decryption=True)

    assert len(skipped) == 1
    assert ctx.decrypt_aborted_reason and "wrong key for KID kid" in ctx.decrypt_aborted_reason


def test_two_workers_racing_on_a_poisoned_kid_still_skip_once():
    host = object.__new__(WorkerMixin)
    skipped = []
    # Both workers must have passed the "already aborted?" check before either records the reason: the barrier forces
    # that interleaving, so the test fails every time (not just sometimes) if the skip is not guarded by the lock.
    both_checked = threading.Barrier(2)

    def poisoned(kids):
        try:
            both_checked.wait(timeout=10)
        except threading.BrokenBarrierError:
            pass
        return "kid"

    host._stream_kids = lambda stream: {"kid"}
    host._kids_poisoned = poisoned
    host._skip_stream_wrong_key = lambda *a, **k: skipped.append(a)
    ctx = _worker_ctx(segments=2)
    ctx.decrypt_queue.put(None)

    threads = [threading.Thread(target=host._decrypt_worker, args=(ctx, True)) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(skipped) == 1
