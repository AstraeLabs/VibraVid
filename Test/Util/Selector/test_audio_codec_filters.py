# 03.11.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest
from mock_streams import MockStream

from VibraVid.core.utils.selector import StreamSelector


def _audio(codec_for_aac: str = "mp4a.40.5") -> list:
    """Italian + English tracks: a Dolby Digital Plus (best by bitrate), an HE-AAC and an AAC-LC one each."""
    streams = []
    for lang in ("it", "en"):
        streams += [
            MockStream(type="audio", id=f"{lang}-ddp", language=lang, resolved_language=lang, codecs="ec-3", bitrate=640_000),
            MockStream(type="audio", id=f"{lang}-he", language=lang, resolved_language=lang, codecs=codec_for_aac, bitrate=192_000),
            MockStream(type="audio", id=f"{lang}-lc", language=lang, resolved_language=lang, codecs="mp4a.40.2", bitrate=128_000),
        ]
    return streams


def _picked(audio_filter: str, streams: list) -> set:
    StreamSelector("best", audio_filter, "false").apply(streams)
    return {s.id for s in streams if s.selected}


def test_without_a_codec_the_best_track_wins():
    assert _picked("ita|eng", _audio()) == {"it-ddp", "en-ddp"}


@pytest.mark.parametrize("spec", ["ita|eng,AAC", "ita|eng,aac", "l=ita|eng:c=aac", "l=ita|eng:c=AAC", "l=ita|eng:c=mp4a"])
def test_aac_means_the_whole_family_and_takes_the_best_of_it(spec):
    # HE-AAC (192 kbps) beats AAC-LC (128 kbps)
    assert _picked(spec, _audio()) == {"it-he", "en-he"}


def test_the_exact_object_type_picks_one_aac_flavour():
    assert _picked("l=ita|eng:c=mp4a.40.2", _audio()) == {"it-lc", "en-lc"}
    assert _picked("l=ita|eng:c=mp4a.40.5", _audio()) == {"it-he", "en-he"}


def test_another_family_is_not_confused_with_aac():
    assert _picked("l=ita|eng:c=ec-3", _audio()) == {"it-ddp", "en-ddp"}


@pytest.mark.parametrize("spec", ["ita|eng,AAC", "l=ita|eng:c=aac"])
def test_a_service_that_names_the_family_still_matches(spec):
    """Some services put "aac" in codecs instead of "mp4a.40.x": both spellings of the filter must keep finding it."""
    streams = [
        MockStream(type="audio", id="it-ddp", language="it", resolved_language="it", codecs="ec-3", bitrate=640_000),
        MockStream(type="audio", id="it-aac", language="it", resolved_language="it", codecs="aac", bitrate=96_000),
    ]
    assert _picked(spec, streams) == {"it-aac"}


def test_an_aac_filter_with_no_aac_track_selects_nothing():
    streams = [MockStream(type="audio", id="it-ddp", language="it", resolved_language="it", codecs="ec-3", bitrate=640_000)]
    assert _picked("ita,AAC", streams) == set()


def test_native_video_codec_is_left_as_written():
    videos = [
        MockStream(type="video", id="avc", height=1080, codecs="avc1.640028", bitrate=7_000_000),
        MockStream(type="video", id="hevc", height=1080, codecs="hvc1.2.4.L120.B0", bitrate=4_000_000),
    ]
    StreamSelector("r=1080:c=hvc1:f=best", "false", "false").apply(videos)
    assert {s.id for s in videos if s.selected} == {"hevc"}
