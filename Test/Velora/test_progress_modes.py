# 01.10.26
# ruff: noqa: E402

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest
from rich.console import Console

from VibraVid.core.ui import bar_manager as bm
from VibraVid.core.ui.tracker import context_tracker

VID = "[bold cyan]Vid[/bold cyan] [H.264] 720p 2.4 Mbps"


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(bm.time, "monotonic", fake)
    return fake


@pytest.fixture
def out(monkeypatch, clock):  # noqa: ARG001
    """Capture what the manager prints (the clock is controllable through the `clock` fixture)."""
    buffer = io.StringIO()
    monkeypatch.setattr(bm, "console", Console(file=buffer, width=200, force_terminal=False, color_system=None))
    context_tracker.is_gui = False
    return buffer


@pytest.fixture(autouse=True)
def _restore_settings(monkeypatch):
    monkeypatch.setattr(bm.DownloadBarManager, "progress_mode", "bars")
    monkeypatch.setattr(bm.DownloadBarManager, "progress_interval", bm.DEFAULT_PROGRESS_INTERVAL)


def _manager(monkeypatch, mode, interval=5):
    bm.DownloadBarManager.configure(mode, interval)
    return bm.DownloadBarManager()


def _update(mgr, key="vid", label=VID, **fields):
    mgr.handle_progress_line({"task_key": key, "label": label, **fields})


def _lines(buffer):
    return [line for line in buffer.getvalue().splitlines() if line.strip()]


# ── modes ─────────────────────────────────────────────────────────────────

def test_default_mode_builds_the_live_bars(out, monkeypatch):
    mgr = _manager(monkeypatch, "bars")
    assert mgr.progress is not None and mgr._live is not None and mgr._mode == "bars"


@pytest.mark.parametrize("mode", ["lines", "none"])
def test_text_modes_build_no_rich_live_objects(out, monkeypatch, mode):
    mgr = _manager(monkeypatch, mode)
    assert mgr.progress is None and mgr._live is None and mgr._mode == mode
    with mgr:  # entering / leaving must be harmless
        _update(mgr, pct=10, speed="1M/s", size="1M / 10M")


def test_unknown_mode_is_rejected(out):
    with pytest.raises(ValueError):
        bm.DownloadBarManager.configure("fancy")


def test_configure_none_leaves_values_unchanged(out):
    bm.DownloadBarManager.configure("lines", 2)
    bm.DownloadBarManager.configure(None, None)
    assert (bm.DownloadBarManager.progress_mode, bm.DownloadBarManager.progress_interval) == ("lines", 2.0)


def test_the_shipped_config_has_no_progress_settings():
    """Progress output is command-line only: nothing in config.json may change it."""
    config = json.loads((workspace_root / "Conf" / "config.json").read_text(encoding="utf-8"))
    assert not {"progress_mode", "progress_interval"} & {key for section in config.values() if isinstance(section, dict) for key in section}


def test_gui_mode_never_uses_text_output(out, monkeypatch):
    context_tracker.is_gui = True
    try:
        mgr = _manager(monkeypatch, "lines")
        assert mgr.progress is None and mgr._mode == "bars"
        _update(mgr, pct=10, speed="1M/s")
        assert _lines(out) == []
    finally:
        context_tracker.is_gui = False


def test_none_mode_prints_nothing(out, monkeypatch):
    mgr = _manager(monkeypatch, "none")
    for pct in (1, 50, 100):
        _update(mgr, pct=pct, speed="9M/s", size="1M / 2M")
    mgr.set_status_text("Muxing")
    assert _lines(out) == []


# ── lines mode ────────────────────────────────────────────────────────────

def test_first_update_prints_a_readable_line(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines")
    _update(mgr, pct=28, size="290.2M / 1012.7M", speed="65.27M/s", eta=11.0, segments="100/356")

    assert _lines(out) == ["Vid [H.264] 720p 2.4 Mbps  28%  290.2M / 1012.7M  65.27M/s  ETA 00:11"]


def test_updates_inside_the_interval_are_dropped(out, clock, monkeypatch):
    mgr = _manager(monkeypatch, "lines", interval=5)
    _update(mgr, pct=10, size="1M / 10M", speed="1M/s", eta=9.0)
    for step in (1, 2, 3, 4):
        clock.now += 1.0
        _update(mgr, pct=10 + step, size=f"{step}M / 10M", speed="1M/s", eta=8.0)
    assert len(_lines(out)) == 1


def test_a_line_is_printed_each_interval_with_the_latest_values(out, clock, monkeypatch):
    mgr = _manager(monkeypatch, "lines", interval=5)
    _update(mgr, pct=10, size="1M / 10M", speed="1M/s", eta=9.0)
    clock.now += 5.0
    _update(mgr, pct=60, size="6M / 10M", speed="2M/s", eta=2.0)

    lines = _lines(out)
    assert len(lines) == 2 and "60%" in lines[1] and "6M / 10M" in lines[1] and "ETA 00:02" in lines[1]


def test_partial_updates_keep_the_previous_values(out, clock, monkeypatch):
    mgr = _manager(monkeypatch, "lines", interval=5)
    _update(mgr, pct=10, size="1M / 10M", speed="1M/s", eta=9.0)
    clock.now += 6.0
    _update(mgr, pct=20)  # a producer that only sends the percentage
    assert "20%" in _lines(out)[1] and "1M / 10M" in _lines(out)[1]


def test_completion_prints_once_even_inside_the_interval(out, clock, monkeypatch):
    mgr = _manager(monkeypatch, "lines", interval=60)
    _update(mgr, pct=10, size="1M / 10M", speed="1M/s", eta=9.0)
    _update(mgr, pct=100, size="10M / 10M", speed="1M/s", eta=0.0)
    _update(mgr, pct=100, size="10M / 10M", speed="1M/s", eta=0.0)
    clock.now += 120.0
    _update(mgr, pct=100, size="10M / 10M", speed="1M/s", eta=0.0)

    lines = _lines(out)
    assert len(lines) == 2 and "100%" in lines[1] and "ETA" not in lines[1]


def test_phase_changes_are_printed_immediately(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines", interval=60)
    _update(mgr, pct=90, size="9M / 10M", speed="1M/s", eta=1.0)
    _update(mgr, pct=100, speed="Merge")
    _update(mgr, pct=100, speed="Merge")  # same phase: not repeated
    _update(mgr, pct=100, speed="Decrypt")

    lines = _lines(out)
    assert [("Merge" in line, "Decrypt" in line) for line in lines[1:]] == [(True, False), (False, True)]


def test_each_track_has_its_own_timer(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines", interval=5)
    _update(mgr, "vid", VID, pct=10, size="1M / 10M", speed="1M/s")
    _update(mgr, "aud", "[bold cyan]Aud[/bold cyan] [AAC] it-IT 192 Kbps", pct=50, size="5M / 10M", speed="1M/s")
    lines = _lines(out)
    assert len(lines) == 2 and lines[0].startswith("Vid") and lines[1].startswith("Aud")


def test_sizes_are_spaced_like_the_bar(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines")
    _update(mgr, pct=1, size="243K/28.5M", speed="1M/s")
    assert "243K / 28.5M" in _lines(out)[0]


def test_a_size_without_a_total_is_kept_as_is(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines")
    _update(mgr, pct=1, size="95K", speed="1M/s")
    assert "  95K  " in _lines(out)[0]


def test_empty_size_placeholders_are_hidden(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines")
    _update(mgr, pct=0, size="0B/0B", speed="0Bps")
    assert "0B/0B" not in _lines(out)[0]


def test_literal_brackets_in_labels_survive(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines")
    _update(mgr, "sub", r"Sub \[srt] it-IT", pct=100, final_size="95K")
    assert _lines(out)[0].startswith("Sub [srt] it-IT") and "95K" in _lines(out)[0]


def test_markup_in_values_is_not_interpreted(out, monkeypatch):
    mgr = _manager(monkeypatch, "lines")
    _update(mgr, pct=5, speed="[bold]fast[/bold]")
    assert "[bold]fast[/bold]" in _lines(out)[0]


def test_status_text_is_printed_only_in_lines_mode(out, monkeypatch):
    _manager(monkeypatch, "lines").set_status_text("[cyan]Muxing[/cyan]")
    assert _lines(out) == ["Muxing"]


def test_subtitle_sizes_are_still_recorded_in_text_modes(out, monkeypatch):
    for mode in ("lines", "none"):
        mgr = _manager(monkeypatch, mode)
        mgr.handle_progress_line({"task_key": "sub_it_srt", "label": "Sub", "final_size": "95K", "_lang_code": "it", "codec": "srt"})
        assert mgr.subtitle_sizes == {"it:srt": "95K"}


def test_interval_has_a_floor(out, monkeypatch):
    assert _manager(monkeypatch, "lines", interval=0)._line_interval == 0.5
    assert _manager(monkeypatch, "lines", interval=2)._line_interval == 2.0


def test_tracker_still_receives_updates_in_text_modes(out, monkeypatch):
    mgr = _manager(monkeypatch, "none")
    mgr.download_id = "dl-1"
    with patch.object(bm.download_tracker, "update_progress") as tracker:
        _update(mgr, pct=42, speed="1M/s", size="4M / 10M", segments="42/100")
    assert tracker.call_args.args[:2] == ("dl-1", "vid") and tracker.call_args.args[2] == 42


def test_silent_manager_is_unaffected():
    from VibraVid.core.velora.util._stream_helpers import SilentDownloadBarManager

    silent = SilentDownloadBarManager("x")
    assert silent.handle_progress_line({"task_key": "a", "pct": 1}) is None
