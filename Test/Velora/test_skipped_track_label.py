# 05.10.26
# ruff: noqa: E402

import sys
import threading
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest
from rich.text import Text

from VibraVid.core.manifest.stream import Stream
from VibraVid.core.velora.downloader import MediaDownloader


class Bars:
    def __init__(self):
        self.lines = []

    def handle_progress_line(self, line):
        self.lines.append(line)


def _downloader():
    dl = object.__new__(MediaDownloader)
    dl._video_labels_by_task_key = {}
    dl._video_label = ""
    dl._audio_labels_by_task_key = {}
    dl._audio_labels = {}
    dl._sub_labels_by_task_key = {}
    dl._decrypt_failures_lock = threading.Lock()
    dl.decrypt_failures = []
    dl._record_track_done = lambda *a, **k: None
    return dl


def _audio(**kw):
    base = dict(type="audio", format="hls", language="en", resolved_language="en-US", codecs="mp4a.40.2", default=True)
    base.update(kw)
    return Stream(**base)


def _plain(markup):
    return Text.from_markup(markup).plain


def test_audio_label_format():
    assert _plain(MediaDownloader._audio_stream_label(_audio())) == "[AAC] en-US [DEFAULT]"
    assert _plain(MediaDownloader._audio_stream_label(_audio(default=False, bitrate=128_000))).startswith("[AAC] en-US 128")


def test_skipped_audio_without_key_gets_its_real_label():
    dl, bars = _downloader(), Bars()
    dl._skip_stream_no_key(_audio(), "aabbcc", bars)

    (line,) = bars.lines
    assert line["task_key"] == "aud_en" and line["speed"] == "Skipped" and line["pct"] == 100
    assert _plain(line["label"]) == "Aud [AAC] en-US [DEFAULT]"


def test_skipped_audio_keeps_the_label_from_the_tables_when_present():
    dl, bars = _downloader(), Bars()
    dl._audio_labels_by_task_key["aud_en"] = r"[yellow]\[EAC3][/yellow] en-US"
    dl._skip_stream_no_key(_audio(), "aabbcc", bars)

    assert _plain(bars.lines[0]["label"]) == "Aud [EAC3] en-US"


def test_skipped_subtitle_gets_its_real_label():
    dl, bars = _downloader(), Bars()
    sub = Stream(type="subtitle", format="hls", language="it", resolved_language="it-IT")
    dl._skip_stream_no_key(sub, "aabbcc", bars)

    line = bars.lines[0]
    assert line["task_key"] == "sub_it" and _plain(line["label"]).startswith("Sub ")
    assert "it-IT" in _plain(line["label"])


def test_skipped_video_rows_are_never_renamed():
    """The video rows are pre-created ("Vid ..." and "Vid DV ..." for the Dolby Vision companion): a label here would rename them."""
    dl, bars = _downloader(), Bars()
    dl._video_task_key = "vid_1440p_hevc"
    dl._video_label = r"[yellow]\[H.265][/yellow] 1440p"
    dl._video_labels_by_task_key = {"vid_1440p_hevc": dl._video_label, "vid_1440p_hevc_dv": r"[yellow]\[Dolby Vision][/yellow] 240p"}
    main = Stream(type="video", format="hls", codecs="hvc1.2.4.L153.B0")
    companion = Stream(type="video", format="hls", codecs="dvh1.05.01")
    companion.dv_companion = True  # set by the StreamSelector, not a constructor argument

    dl._skip_stream_no_key(main, "aabbcc", bars)
    dl._skip_stream_no_key(companion, "aabbcc", bars)
    dl._skip_stream_wrong_key(companion, "aabbcc", bars)

    assert [line["task_key"] for line in bars.lines] == ["vid_1440p_hevc", "vid_1440p_hevc_dv", "vid_1440p_hevc_dv"]
    assert all("label" not in line for line in bars.lines)


def test_wrong_key_failure_row_has_the_label_too():
    dl, bars = _downloader(), Bars()
    dl._skip_stream_wrong_key(_audio(), "aabbcc", bars)

    line = bars.lines[0]
    assert line["speed"] == "Failed" and _plain(line["label"]) == "Aud [AAC] en-US [DEFAULT]"


@pytest.mark.parametrize("bar_manager", [None])
def test_no_bar_manager_is_a_noop(bar_manager):
    dl = _downloader()
    dl._skip_stream_no_key(_audio(), "aabbcc", bar_manager)
    assert dl.decrypt_failures and dl.decrypt_failures[0]["skipped"] is True
