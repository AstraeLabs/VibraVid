# 28.11.26
# ruff: noqa: E402

"""select_audio with ";" groups: each group is its own filter and the picks add up ("ita|eng,AAC;ger,EAC3")."""

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest
from mock_streams import MockStream

from VibraVid.core.utils.audio_selection import configured_audio_languages
from VibraVid.core.utils.selector import (
    StreamSelector,
    filter_language_groups,
    filter_language_order,
    split_filter_groups,
)


def _audio(langs=("it", "en", "de")) -> list:
    """An E-AC-3 (best by bitrate) and an AAC track for every language."""
    streams = []
    for lang in langs:
        streams += [
            MockStream(type="audio", id=f"{lang}-ddp", language=lang, resolved_language=lang, codecs="ec-3", bitrate=640_000),
            MockStream(type="audio", id=f"{lang}-aac", language=lang, resolved_language=lang, codecs="mp4a.40.2", bitrate=128_000),
        ]
    return streams


def _picked(audio_filter: str, streams: list, **kwargs) -> set:
    selector = StreamSelector("best", audio_filter, "false", **kwargs)
    selector.apply(streams)
    return {s.id for s in streams if s.selected}


@pytest.mark.parametrize("value", ["ita|eng,AAC;ger,EAC3", "l=ita|eng:c=aac;l=ger:c=ec-3", " ita|eng,AAC ; ger,EAC3 "])
def test_each_group_keeps_its_own_codec(value):
    assert _picked(value, _audio()) == {"it-aac", "en-aac", "de-ddp"}


def test_a_group_never_takes_the_codec_of_another_language():
    # Only German has E-AC-3 here: the second group must not reach for the Italian one
    streams = _audio(("it", "en")) + [MockStream(type="audio", id="de-ddp", language="de", resolved_language="de", codecs="ec-3", bitrate=640_000)]
    streams = [s for s in streams if s.id != "it-ddp"]
    assert _picked("ita|eng,AAC;ger,EAC3", streams) == {"it-aac", "en-aac", "de-ddp"}


def test_a_language_missing_from_the_title_is_skipped():
    assert _picked("ita|eng,AAC;ger,EAC3", _audio(("it", "en"))) == {"it-aac", "en-aac"}


def test_a_missing_codec_keeps_the_language_with_its_best_track():
    streams = [s for s in _audio() if s.id != "de-ddp"]
    assert _picked("ita|eng,AAC;ger,EAC3", streams) == {"it-aac", "en-aac", "de-aac"}


def test_strict_mode_skips_the_download_when_a_group_has_no_match():
    streams = [s for s in _audio() if s.id != "de-ddp"]
    selector = StreamSelector("best", "ita|eng,AAC;ger,EAC3", "false", strict_no_match=True)
    selector.apply(streams)
    assert selector.no_match


def test_a_group_without_codec_takes_the_best_track():
    assert _picked("ita,AAC;ger", _audio()) == {"it-aac", "de-ddp"}


def test_the_formatter_names_every_picked_track():
    streams = _audio()
    _, sa, _ = StreamSelector("best", "ita|eng,AAC;ger,EAC3", "false").apply(streams)
    for stream_id in ("it-aac", "en-aac", "de-ddp"):
        assert stream_id in sa


@pytest.mark.parametrize("value", ["ita|eng,AAC", "1ita|2eng", "best", "all"])
def test_a_value_without_semicolon_is_unchanged(value):
    assert split_filter_groups(value) == [value]


def test_language_helpers_read_every_group():
    value = "ita|eng,AAC;ger,EAC3"
    assert split_filter_groups(value) == ["ita|eng,AAC", "ger,EAC3"]
    assert filter_language_groups(value, "audio") == ["ita|eng|ger"]
    assert filter_language_order(value, "audio") == ["ita", "eng", "ger"]
    assert [c["code"] for c in configured_audio_languages(value)] == ["it", "en", "de"]
    # slots alone keep one entry per slot
    assert filter_language_groups("1ita|2eng", "audio") == ["ita", "eng"]
    assert filter_language_groups("all", "audio") == []
