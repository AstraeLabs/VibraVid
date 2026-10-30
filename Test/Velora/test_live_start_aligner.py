"""LiveStartAligner: the live tracks of a download start from the same instant; a track that never reports does not stall the others."""

import struct
import threading
from types import SimpleNamespace

from VibraVid.core.velora import _live_align
from VibraVid.core.velora._live_align import LiveStartAligner
from VibraVid.core.velora.util._tfdt_rebase import track_timescale


def _track(kind="video", **extra):
    return SimpleNamespace(type=kind, selected=True, is_external=False, is_live=True, **extra)


def _run(*targets, timeout=10):
    threads = [threading.Thread(target=target, daemon=True) for target in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    assert not any(thread.is_alive() for thread in threads), "a track thread is stuck"


def test_every_track_gets_the_latest_start():
    video, audio = _track("video"), _track("audio")
    aligner = LiveStartAligner([video, audio])
    got = {}

    _run(
        lambda: got.__setitem__("video", aligner.common_start(video, 1000.00, lambda: False)),
        lambda: got.__setitem__("audio", aligner.common_start(audio, 998.08, lambda: False)),
    )

    assert got == {"video": 1000.00, "audio": 1000.00}


def test_tracks_that_already_agree_keep_their_start():
    video, audio = _track("video"), _track("audio")
    aligner = LiveStartAligner([video, audio])
    got = {}

    _run(
        lambda: got.__setitem__("video", aligner.common_start(video, 500.0, lambda: False)),
        lambda: got.__setitem__("audio", aligner.common_start(audio, 500.0, lambda: False)),
    )

    assert set(got.values()) == {500.0}


def test_a_track_that_leaves_without_reporting_does_not_stall_the_others():
    video, audio = _track("video"), _track("audio")
    aligner = LiveStartAligner([video, audio])
    got = {}

    _run(
        lambda: got.__setitem__("video", aligner.common_start(video, 42.0, lambda: False)),
        lambda: aligner.leave(audio),
    )

    assert got["video"] == 42.0


def test_a_stop_while_waiting_gives_no_alignment():
    video, audio = _track("video"), _track("audio")
    aligner = LiveStartAligner([video, audio])
    got = {}

    _run(lambda: got.__setitem__("video", aligner.common_start(video, 42.0, lambda: True)))

    assert got["video"] is None


def test_waiting_has_a_time_limit(monkeypatch):
    monkeypatch.setattr(_live_align, "ALIGN_TIMEOUT_SECONDS", 0.2)
    video, audio = _track("video"), _track("audio")
    aligner = LiveStartAligner([video, audio])
    got = {}

    _run(lambda: got.__setitem__("video", aligner.common_start(video, 42.0, lambda: False)))

    assert got["video"] is None


def test_an_unknown_track_is_not_aligned():
    aligner = LiveStartAligner([_track("video"), _track("audio")])

    assert aligner.common_start(_track("video"), 1.0, lambda: False) is None


def test_a_single_track_needs_no_aligner():
    owner = SimpleNamespace(streams=[_track("video"), _track("subtitle")])

    assert LiveStartAligner.create(owner) is None


def test_create_takes_only_the_selected_live_media_tracks():
    video, audio = _track("video"), _track("audio")
    skipped = SimpleNamespace(type="audio", selected=False, is_external=False, is_live=True)
    external = SimpleNamespace(type="audio", selected=True, is_external=True, is_live=True)
    not_live = SimpleNamespace(type="audio", selected=True, is_external=False, is_live=False)
    owner = SimpleNamespace(streams=[video, audio, skipped, external, not_live])

    aligner = LiveStartAligner.create(owner)

    assert aligner is not None
    assert aligner._tracks == [video, audio]


def _mdhd(version: int, timescale: int) -> bytes:
    if version == 0:
        body = bytes([0, 0, 0, 0]) + struct.pack(">III", 0, 0, timescale) + struct.pack(">I", 0)
    else:
        body = bytes([1, 0, 0, 0]) + struct.pack(">QQ", 0, 0) + struct.pack(">I", timescale) + struct.pack(">Q", 0)
    return struct.pack(">I", 8 + len(body)) + b"mdhd" + body


def test_track_timescale_reads_both_mdhd_versions(tmp_path):
    for version, scale in ((0, 90000), (1, 48000)):
        init = tmp_path / f"init{version}.mp4"
        init.write_bytes(b"\x00" * 16 + _mdhd(version, scale) + b"\x00" * 16)
        assert track_timescale(init) == scale


def test_track_timescale_without_mdhd_is_none(tmp_path):
    init = tmp_path / "init.mp4"
    init.write_bytes(b"\x00" * 64)

    assert track_timescale(init) is None
    assert track_timescale(tmp_path / "missing.mp4") is None
