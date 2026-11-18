# 13.09.26
# ruff: noqa: E402

import sys
from pathlib import Path


workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))


from mock_streams import (
    create_audio_streams_example1,
    create_video_streams_example2,
)
from VibraVid.core.utils.selector import StreamSelector


def _selected_video(streams):
    return [s for s in streams if s.type == "video" and s.selected]


def _selected_audio(streams):
    return [s for s in streams if s.type == "audio" and s.selected]


def test_audio_no_match_falls_back_when_not_strict():
    """Default behaviour (no --skip-no-match): unmatched language falls back to best available."""
    streams = create_audio_streams_example1()
    selector = StreamSelector("false", "kor", "false", strict_no_match=False)
    selector.apply(streams)

    assert selector.no_match is False
    assert len(_selected_audio(streams)) > 0, "should fall back to best-available audio, not drop"


def test_audio_no_match_drops_track_when_strict():
    """With --skip-no-match: an unmatched language must not fall back — no audio selected."""
    streams = create_audio_streams_example1()
    selector = StreamSelector("false", "kor", "false", strict_no_match=True)
    selector.apply(streams)

    assert selector.no_match is True
    assert _selected_audio(streams) == []


def test_audio_match_is_unaffected_by_strict_mode():
    """A language that DOES exist must still be selected normally under strict mode."""
    streams = create_audio_streams_example1()
    selector = StreamSelector("false", "ita", "false", strict_no_match=True)
    selector.apply(streams)

    assert selector.no_match is False
    sel = _selected_audio(streams)
    assert len(sel) == 1
    assert sel[0].resolved_language == "it-IT"


def test_video_no_match_falls_back_when_not_strict():
    """Default behaviour: an unavailable resolution falls back to nearest/best."""
    streams = create_video_streams_example2()  # no 1080p available
    selector = StreamSelector("1080", "false", "false", strict_no_match=False)
    selector.apply(streams)

    assert selector.no_match is False
    assert len(_selected_video(streams)) == 1


def test_video_no_match_drops_track_when_strict():
    """With --skip-no-match: requesting a resolution the source doesn't have must signal no_match."""
    streams = create_video_streams_example2()  # no 1080p available
    selector = StreamSelector("1080", "false", "false", strict_no_match=True)
    selector.apply(streams)

    assert selector.no_match is True
    assert _selected_video(streams) == []


def test_video_match_is_unaffected_by_strict_mode():
    """A resolution that DOES exist must still be selected normally under strict mode."""
    streams = create_video_streams_example2()
    selector = StreamSelector("720", "false", "false", strict_no_match=True)
    selector.apply(streams)

    assert selector.no_match is False
    sel = _selected_video(streams)
    assert len(sel) == 1
    assert sel[0].height == 720


def test_no_match_is_true_if_any_track_type_misses_even_when_others_match():
    """--skip-no-match is all-or-nothing: one unmatched track type is enough to flag no_match,
    even if the other requested track types matched fine (mirrors the CLI's whole-download skip)."""
    video_streams = create_video_streams_example2()  # only up to 720p
    audio_streams = create_audio_streams_example1()  # has "ita"
    streams = video_streams + audio_streams

    selector = StreamSelector("1080", "ita", "false", strict_no_match=True)
    selector.apply(streams)

    assert selector.no_match is True, "video 1080p has no match, so the whole selection must be flagged"
    assert len(_selected_audio(streams)) == 1, "audio still resolves independently to the matched language"
