# 16.09.26

import re

from VibraVid.core.muxing.helper.sub.ttml import convert_ttml_to_format


def _tt_block(begin_end_text: list[tuple[str, str, str]]) -> bytes:
    cues = "".join(
        f'<p begin="{begin}" end="{end}">{text}</p>' for begin, end, text in begin_end_text
    )
    return (
        '<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en">'
        f"<body><div>{cues}</div></body></tt>"
    ).encode("utf-8")


def _cue_times(srt_text: str) -> list[tuple[str, str]]:
    return re.findall(r"(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})", srt_text)


def test_already_absolute_blocks_are_not_double_shifted(tmp_path):
    # Mirrors the Canal+ DASH stpp case: each ~1-minute block already carries
    # absolute program time and must NOT be re-based on top of itself.
    blocks = [
        _tt_block([("00:00:08.000", "00:00:10.000", "one")]),
        _tt_block([("00:01:00.000", "00:01:02.000", "two")]),
        _tt_block([("00:02:03.000", "00:02:05.000", "three")]),
    ]
    src = tmp_path / "sub.mp4"
    src.write_bytes(b"".join(blocks))
    out = tmp_path / "sub.srt"

    assert convert_ttml_to_format(str(src), str(out), "srt")
    times = _cue_times(out.read_text(encoding="utf-8"))
    assert times == [
        ("00:00:08,000", "00:00:10,000"),
        ("00:01:00,000", "00:01:02,000"),
        ("00:02:03,000", "00:02:05,000"),
    ]


def test_zero_restarting_blocks_are_rebased_ism_style(tmp_path):
    # Genuine ISM-style chunking: each block's own cues restart near zero and
    # must be shifted onto the running timeline. Chunks are long enough (~5-9s)
    # that the running offset clearly outgrows the near-zero restart tolerance.
    blocks = [
        _tt_block([("00:00:01.000", "00:00:06.000", "one")]),
        _tt_block([("00:00:00.500", "00:00:09.000", "two")]),
        _tt_block([("00:00:00.200", "00:00:08.000", "three")]),
    ]
    src = tmp_path / "sub.mp4"
    src.write_bytes(b"".join(blocks))
    out = tmp_path / "sub.srt"

    assert convert_ttml_to_format(str(src), str(out), "srt")
    times = _cue_times(out.read_text(encoding="utf-8"))
    assert times == [
        ("00:00:01,000", "00:00:06,000"),
        ("00:00:06,500", "00:00:15,000"),
        ("00:00:15,200", "00:00:23,000"),
    ]
