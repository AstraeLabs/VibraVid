# 28.10.26

import logging

from VibraVid.core.manifest.custom import CustomParser
from VibraVid.core.manifest.stream import Stream
from VibraVid.core.velora.util._subtitle_segments import merge_vtt_files

LOG = logging.getLogger("test_vtt_time_origin")


def _vtt(tmp_path, name, cues):
    body = ["WEBVTT", ""]
    for start, end, text in cues:
        body += [f"{start} --> {end}", text, ""]
    path = tmp_path / name
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def _starts(merged):
    return [line.split(" --> ")[0] for line in merged.splitlines() if " --> " in line]


def test_stream_defaults_to_no_time_origin():
    assert Stream(type="subtitle").time_origin == 0.0


def test_default_merge_is_unchanged(tmp_path):
    seg = _vtt(tmp_path, "a.vtt", [("00:00:05.000", "00:00:07.000", "hello")])
    assert _starts(merge_vtt_files([seg], LOG)) == ["00:00:05.000"]
    assert _starts(merge_vtt_files([seg], LOG, time_origin=0.0)) == ["00:00:05.000"]


def test_time_origin_is_subtracted_from_absolute_cues(tmp_path):
    """Live subtitles on an absolute timeline (here from 1h) must start at zero like the media fragments."""
    segs = [
        _vtt(tmp_path, "a.vtt", [("01:00:05.000", "01:00:07.000", "first")]),
        _vtt(tmp_path, "b.vtt", [("01:00:11.000", "01:00:13.500", "second")]),
    ]
    merged = merge_vtt_files(segs, LOG, time_origin=3600.0)
    assert _starts(merged) == ["00:00:05.000", "00:00:11.000"]
    assert "00:00:07.000" in merged and "00:00:13.500" in merged


def test_cue_ending_before_the_origin_is_dropped(tmp_path):
    seg = _vtt(tmp_path, "a.vtt", [("00:59:50.000", "00:59:55.000", "before"), ("01:00:05.000", "01:00:07.000", "after")])
    merged = merge_vtt_files([seg], LOG, time_origin=3600.0)
    assert "before" not in merged
    assert _starts(merged) == ["00:00:05.000"]


def test_cue_straddling_the_origin_is_clamped_to_zero(tmp_path):
    seg = _vtt(tmp_path, "a.vtt", [("00:59:58.000", "01:00:02.000", "straddle")])
    merged = merge_vtt_files([seg], LOG, time_origin=3600.0)
    assert _starts(merged) == ["00:00:00.000"]
    assert "00:00:02.000" in merged


SEGMENTS = [{"url": "https://example.test/sub-1.vtt"}]


def _track(**extra):
    return {"type": "subtitle", "id": "sub", "language": "it", "format": "vtt", "segments": SEGMENTS, **extra}


def test_custom_manifest_reads_time_origin():
    parser = CustomParser.__new__(CustomParser)
    assert parser._build_stream(0, _track(time_origin=3600.5), 0.0).time_origin == 3600.5
    assert parser._build_stream(0, _track(time_origin=None), 0.0).time_origin == 0.0
    assert parser._build_stream(0, _track(), 0.0).time_origin == 0.0


def test_custom_manifest_ignores_time_origin_on_other_track_types():
    parser = CustomParser.__new__(CustomParser)
    audio = parser._build_stream(0, {"type": "audio", "id": "a", "segments": SEGMENTS, "time_origin": 99}, 0.0)
    assert audio.time_origin == 0.0
