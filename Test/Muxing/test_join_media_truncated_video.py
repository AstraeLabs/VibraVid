# 24.10.26

"""``join_media`` must refuse a video that is far shorter than its audio instead of cutting every track to it (-t/-shortest) and reporting success."""

import pytest

from VibraVid.core.muxing import merge

VIDEO = "video.mp4"
AUDIOS = [{"path": "a1.m4a", "name": "ita"}, {"path": "a2.m4a", "name": "eng"}]


@pytest.fixture
def mux(monkeypatch, tmp_path):
    """Fake the probes and the ffmpeg mux; ``durations[path]`` drives get_video_duration, ``calls`` counts real mux runs."""
    durations: dict[str, float | None] = {}
    calls: list[str] = []
    out = tmp_path / "out.mkv"

    def fake_join(video_path, audio_tracks, subtitle_tracks, out_path, *args, **kwargs):
        calls.append(video_path)
        out.write_bytes(b"muxed")
        return str(out), {"exit_code": 0}

    monkeypatch.setattr(merge, "get_video_duration", lambda path: durations.get(str(path)))
    monkeypatch.setattr(merge, "_prepare_audio_tracks", lambda video_path, tracks, limit: (list(tracks), False))
    monkeypatch.setattr(merge, "_join_media_ffmpeg", fake_join)
    monkeypatch.setattr(merge, "_apply_compatible_extension", lambda video_path, out_path: out_path)
    monkeypatch.setattr(merge, "_ensure_video_kept", lambda video_path, merged, result_json: result_json)
    monkeypatch.setattr(merge, "MUX_ENGINE", "ffmpeg")
    monkeypatch.setattr(merge, "get_mkvmerge_path", lambda: None)
    monkeypatch.setattr(merge, "has_unsupported_ffmpeg_audio_codec", lambda tracks: False)
    return durations, calls, out


def _run(tmp_path, tracks=AUDIOS):
    return merge.join_media(VIDEO, [dict(t) for t in tracks], [], str(tmp_path / "out.mkv"))


def test_video_cut_to_a_fraction_of_the_audio_is_not_muxed(mux, tmp_path):
    durations, calls, out = mux
    durations.update({VIDEO: 2898.9, "a1.m4a": 6957.0, "a2.m4a": 6957.0})

    merged, result = _run(tmp_path)

    assert merged == "" and result["video_truncated"] is True and result["exit_code"] == 1
    assert not calls and not out.exists()


def test_a_normal_download_is_muxed(mux, tmp_path):
    durations, calls, out = mux
    durations.update({VIDEO: 6955.0, "a1.m4a": 6957.0, "a2.m4a": 6957.0})

    merged, result = _run(tmp_path)

    assert merged == str(out) and "video_truncated" not in result and calls == [VIDEO]


def test_a_short_clip_with_a_longer_audio_tail_is_still_muxed(mux, tmp_path):
    durations, calls, _out = mux
    durations.update({VIDEO: 20.0, "a1.m4a": 40.0, "a2.m4a": 40.0})  # past the ratio, but under the minimum gap

    _merged, result = _run(tmp_path)

    assert "video_truncated" not in result and calls == [VIDEO]


def test_one_audio_matching_the_video_keeps_the_mux(mux, tmp_path):
    durations, calls, _out = mux
    durations.update({VIDEO: 3000.0, "a1.m4a": 7000.0, "a2.m4a": 3001.0})  # only one track is long: not conclusive

    _merged, result = _run(tmp_path)

    assert "video_truncated" not in result and calls == [VIDEO]


@pytest.mark.parametrize("missing", [VIDEO, "a1.m4a"])
def test_unknown_durations_never_block_the_mux(mux, tmp_path, missing):
    durations, calls, _out = mux
    durations.update({VIDEO: 2898.9, "a1.m4a": 6957.0, "a2.m4a": 6957.0})
    durations[missing] = None

    _merged, result = _run(tmp_path)

    assert "video_truncated" not in result and calls == [VIDEO]


def test_a_video_without_audio_tracks_is_untouched(mux, tmp_path):
    durations, calls, _out = mux
    durations[VIDEO] = 10.0

    _merged, result = _run(tmp_path, tracks=[])

    assert "video_truncated" not in result and calls == [VIDEO]
