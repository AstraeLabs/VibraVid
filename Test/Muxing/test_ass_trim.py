# 21.10.26

"""ASS/SSA tracks are measured and trimmed too: one event running past the video would stretch the whole mkv."""

import pytest

from VibraVid.core.muxing.helper.sub.sanitize import get_subtitle_duration, trim_subtitle_to_duration

HEADER = (
    "[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,40\n\n"
    "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)
EVENTS = [
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,first, with a comma",
    "Dialogue: 0,0:24:06.79,0:24:39.93,Default,,0,0,0,,{\an7}next episode card",
    "Dialogue: 0,0:24:30.00,0:24:35.00,Default,,0,0,0,,after the end",
]


def write(path, newline="\n", bom=False):
    body = (HEADER + "\n".join(EVENTS) + "\n").replace("\n", newline)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8"))
    return str(path)


def test_duration_is_the_latest_event_end(tmp_path):
    assert get_subtitle_duration(write(tmp_path / "a.ass")) == pytest.approx(24 * 60 + 39.93)


def test_trim_drops_late_events_and_cuts_the_overrunning_one(tmp_path):
    path = write(tmp_path / "a.ass")
    assert trim_subtitle_to_duration(path, 1449.9) == path
    text = (tmp_path / "a.ass").read_text(encoding="utf-8")
    assert "after the end" not in text
    assert "first, with a comma" in text
    assert "Dialogue: 0,0:24:06.79,0:24:09.90,Default,,0,0,0,,{\an7}next episode card" in text
    assert get_subtitle_duration(path) == pytest.approx(1449.9)


@pytest.mark.parametrize("newline,bom", [("\r\n", False), ("\n", True), ("\r\n", True)])
def test_trim_keeps_line_endings_and_bom(tmp_path, newline, bom):
    path = write(tmp_path / "a.ass", newline, bom)
    trim_subtitle_to_duration(path, 1449.9)
    raw = (tmp_path / "a.ass").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") == bom
    assert (b"\r\n" in raw) == (newline == "\r\n")


def test_script_that_already_fits_is_unchanged(tmp_path):
    path = write(tmp_path / "a.ass")
    before = (tmp_path / "a.ass").read_bytes()
    trim_subtitle_to_duration(path, 2000)
    assert (tmp_path / "a.ass").read_bytes() == before


def test_ass_without_events_has_no_duration(tmp_path):
    (tmp_path / "e.ass").write_text("[Script Info]\nTitle: x\n", encoding="utf-8")
    assert get_subtitle_duration(str(tmp_path / "e.ass")) is None
