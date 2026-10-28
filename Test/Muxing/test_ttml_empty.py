# 22.10.26

"""A TTML track made only of cue-less blocks (Prime Video live stpp) is skipped quietly; a real parse failure is still an error."""

from VibraVid.core.muxing.helper.sub.ttml import convert_ttml_to_format

EMPTY_TT = (
    b'<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling" xml:lang="en">'
    b'<head><styling><style xml:id="paragraphStyle" tts:textAlign="center"/></styling></head>'
    b'<body><div/></body></tt>'
)
BROKEN_TT = b'<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="00:00:01.000" end="00:00:02.000">open</div></body></tt>'
CUE_TT = b'<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en"><body><div><p begin="00:00:01.000" end="00:00:02.000">hello</p></div></body></tt>'


def _convert(tmp_path, *blocks):
    src = tmp_path / "sub.mp4"
    src.write_bytes(b"".join(blocks))
    out = tmp_path / "sub.srt"
    return convert_ttml_to_format(str(src), str(out), "srt"), out


def test_empty_blocks_are_skipped_without_an_error(tmp_path, printed):
    ok, out = _convert(tmp_path, *[EMPTY_TT] * 5)

    shown = printed()
    assert ok is False and not out.exists()
    assert "no captions" in shown and "5 empty block(s)" in shown
    assert "No valid TTML blocks processed" not in shown


def test_unparsable_blocks_still_report_the_error(tmp_path, printed):
    ok, _ = _convert(tmp_path, BROKEN_TT)

    shown = printed()
    assert ok is False
    assert "No valid TTML blocks processed" in shown


def test_a_cue_among_empty_blocks_is_still_converted(tmp_path):
    ok, out = _convert(tmp_path, EMPTY_TT, CUE_TT, EMPTY_TT)

    assert ok is True
    assert "hello" in out.read_text(encoding="utf-8")
