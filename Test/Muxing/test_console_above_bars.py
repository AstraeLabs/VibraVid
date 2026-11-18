# 28.10.26

"""The muxing modules print through the console the progress bars live on, so a line written while the bars are up lands above them instead of being overwritten by their next refresh."""

import io
import re

import pytest

from VibraVid.core.muxing import merge
from VibraVid.core.muxing.helper import font_embed
from VibraVid.core.muxing.helper.sub import convert, detect, ttml
from VibraVid.core.ui import bar_manager
from VibraVid.core.ui.bar_manager import DownloadBarManager

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
EMPTY_TT = (
    b'<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en"><head/><body><div/></body></tt>'
)


def _live_bars(monkeypatch):
    """The download bars, drawn into a buffer: a line printed while they are up is either in it (through their console) or on stdout (around it)."""
    buf = io.StringIO()
    monkeypatch.setattr(bar_manager.console, "_file", buf)
    monkeypatch.setattr(bar_manager.console, "_force_terminal", True)
    monkeypatch.setattr(DownloadBarManager, "progress_mode", "bars")
    bars = DownloadBarManager()
    return bars, lambda: _ANSI.sub("", buf.getvalue())


@pytest.mark.parametrize("module", [merge, convert, detect, ttml, font_embed], ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_the_modules_the_live_mux_reaches_share_the_bars_console(module):
    """A module with a ``Console()`` of its own writes straight to the terminal (rich unwraps the Live's stdout proxy), around the bars."""
    assert module.console is bar_manager.console


def test_audio_sync_line_shows_while_the_bars_are_up(monkeypatch, printed):
    """The live mux builds its ffmpeg command, and so runs ``audio_sync``, while the download bars are still live."""
    monkeypatch.setattr(merge, "get_video_duration", lambda *_: 3483.0)
    bars, shown = _live_bars(monkeypatch)

    with bars:
        bars.add_prebuilt_tasks([("video", "Video"), ("audio", "Audio")])
        merge.audio_sync(3483.0, ["a.m4a"], ["Italian"])

    assert "Audio lang Italian, Video: 3483s, Diff: +0s" in shown()
    assert "Audio lang" not in printed()


def test_subtitle_conversion_notice_shows_while_the_bars_are_up(monkeypatch, tmp_path, printed):
    """The live mux converts its subtitles (``convert_subtitle`` -> TTML) in that same command-building step."""
    src = tmp_path / "sub.mp4"
    src.write_bytes(EMPTY_TT * 5)
    bars, shown = _live_bars(monkeypatch)

    with bars:
        bars.add_prebuilt_tasks([("video", "Video"), ("subtitle", "Subtitle")])
        assert ttml.convert_ttml_to_format(str(src), str(tmp_path / "sub.srt"), "srt") is False

    assert "5 empty block(s)" in shown()
    assert "empty block" not in printed()
