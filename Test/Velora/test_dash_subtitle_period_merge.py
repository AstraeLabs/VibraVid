# 28.09.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

from VibraVid.core.velora._multiperiod import (
    _merge_plain_subtitle_parts,
    _vtt_cue_bounds,
    _vtt_strip_header,
    _vtt_timeline_is_absolute,
)

# Real HBO Max values, measured from the Superman (2025) MPD served on
# 2026-09-28. Each Period ships a single text/vtt Representation carrying an
# X-TIMESTAMP-MAP of LOCAL:00:00:00.000,MPEGTS:0 -- an identity mapping, so the
# cue times are already on the presentation timeline rather than Period-relative.
_PERIOD_DURATIONS = [993.075, 952.2, 1033.2, 920.5, 993.8, 1589.4]
_PERIOD_OFFSETS = [0.0, 993.075, 1945.275, 2978.475, 3898.975, 4892.775]

_ABSOLUTE_CUES = [
    (5.130, 8.217, "line:17% position:50%", "[musica trionfale in crescendo]"),
    (1005.1, 1008.0, "", "Primo Periodo"),
    (1945.8, 1948.0, "", "Secondo Periodo"),
    (2984.0, 2986.0, "", "Terzo Periodo"),
    (3905.4, 3907.0, "", "Quarto Periodo"),
    (4893.6, 6060.7, "", "Quinto Periodo"),
]

# What ffmpeg's concat demuxer produced for the very same payloads: each Period
# re-offset by the accumulated duration of the previous ones.
_CONCAT_DEMUXER_END = 20733.6


def _part(index: int, cues: list[tuple[float, float, str, str]]) -> str:
    body = ["WEBVTT", "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0", ""]
    for start, end, settings, text in cues:
        line = f"{_stamp(start)} --> {_stamp(end)}"
        if settings:
            line += f" {settings}"
        body.append(line)
        body.append(text)
        body.append("")
    return "\n".join(body)


def _stamp(value: float) -> str:
    h = int(value // 3600)
    m = int((value % 3600) // 60)
    s = value - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _write_parts(tmp: Path, payloads: list[str]) -> list[Path]:
    parts = []
    for idx, payload in enumerate(payloads):
        p = tmp / f"period_{idx:03d}.vtt"
        p.write_text(payload, encoding="utf-8")
        parts.append(p)
    return parts


def _merge(tmp: Path, payloads: list[str], offsets: list[float] | None = None):
    parts = _write_parts(tmp, payloads)
    out = tmp / "merged.vtt"
    basis = _merge_plain_subtitle_parts(parts, offsets if offsets is not None else _PERIOD_OFFSETS, out)
    return out.read_text(encoding="utf-8"), basis


def test_absolute_hbo_cues_are_not_shifted_a_second_time(tmp_path: Path):
    """The real bug: HBO cues already sit on the presentation timeline, so the
    concat demuxer re-offset them and stretched the track to 20733s instead of
    6060s -- which is what inflated the muxed container to 5h51m."""
    text, basis = _merge(tmp_path, [_part(i, [c]) for i, c in enumerate(_ABSOLUTE_CUES)])

    assert basis == "absolute"
    _, last_end = _vtt_cue_bounds(text)
    assert last_end == 6060.7
    assert last_end < _CONCAT_DEMUXER_END / 3


def test_absolute_hbo_cues_stay_chronological_and_never_overlap(tmp_path: Path):
    text, _ = _merge(tmp_path, [_part(i, [c]) for i, c in enumerate(_ABSOLUTE_CUES)])

    ends: list[float] = []
    previous = None
    for line in text.splitlines():
        if " --> " not in line:
            continue
        start = _to_s(line.split(" --> ")[0])
        end = _to_s(line.split(" --> ")[1].split(" ")[0])
        if previous is not None:
            assert start >= previous, f"cue at {start} overlaps the previous one ending at {previous}"
        previous = end
        ends.append(end)

    assert len(ends) == len(_ABSOLUTE_CUES)
    assert max(ends) == 6060.7


def test_period_relative_cues_are_shifted_onto_the_presentation_timeline(tmp_path: Path):
    """Providers that emit Period-relative cues still need the Period start added."""
    relative = [_part(i, [(5.0, 20.0, "", f"Periodo {i}")]) for i in range(6)]
    text, basis = _merge(tmp_path, relative)

    assert basis == "period-relative (shifted)"
    cues = [line for line in text.splitlines() if " --> " in line]
    assert len(cues) == 6
    for idx, line in enumerate(cues):
        start = _to_s(line.split(" --> ")[0])
        assert start == 5.0 + _PERIOD_OFFSETS[idx]


def test_only_one_webvtt_header_survives_the_merge(tmp_path: Path):
    text, _ = _merge(tmp_path, [_part(i, [c]) for i, c in enumerate(_ABSOLUTE_CUES)])

    assert text.count("WEBVTT") == 1
    assert text.startswith("WEBVTT\n")
    assert "X-TIMESTAMP-MAP" not in text


def test_cue_settings_and_multiline_payloads_survive(tmp_path: Path):
    multiline = "00:00:05.130 --> 00:00:08.217 line:17% position:50%\n[suona \"This Summer\"\ndi Sleigh Bells]\n"
    text, _ = _merge(tmp_path, [_part(0, [(5.13, 8.217, "line:17% position:50%", "[musica]")]), "WEBVTT\n\n" + multiline])

    assert "line:17% position:50%" in text
    assert "[musica]" in text


def test_period_without_cues_is_skipped_without_breaking_the_join(tmp_path: Path):
    payloads = [_part(0, [_ABSOLUTE_CUES[0]]), "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n"]
    payloads += [_part(i, [c]) for i, c in enumerate(_ABSOLUTE_CUES[1:], start=1)]
    text, basis = _merge(tmp_path, payloads)

    assert basis == "absolute"
    assert text.count("WEBVTT") == 1
    _, last_end = _vtt_cue_bounds(text)
    assert last_end == 6060.7


def test_strip_header_keeps_cues_when_no_blank_separator_follows():
    payload = "WEBVTT\n00:00:01.000 --> 00:00:02.000\nciao\n"
    assert _vtt_strip_header(payload) == "00:00:01.000 --> 00:00:02.000\nciao"


def test_timeline_detection_flags_relative_payloads():
    absolute = [_part(i, [c]) for i, c in enumerate(_ABSOLUTE_CUES)]
    assert _vtt_timeline_is_absolute(absolute) is True

    relative = [_part(0, [(5.0, 993.0, "", "a")]), _part(1, [(3.0, 400.0, "", "b")])]
    assert _vtt_timeline_is_absolute(relative) is False


def _to_s(stamp: str) -> float:
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)
