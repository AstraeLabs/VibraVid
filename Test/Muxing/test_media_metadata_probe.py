# 23.10.26

"""get_media_metadata() reads fps, bit rate (stream / tags / mkvmerge / packets / container), HDR range, bit depth and audio channels; the new name tokens use them."""

import json
from types import SimpleNamespace

import pytest

from VibraVid.core.downloader._media_tokens import FILE_PROBE_KEYS, media_token_values, resolve_media_tokens, strip_media_tokens
from VibraVid.core.muxing.helper.video import metadata as md


@pytest.mark.parametrize("value, expected", [("24000/1001", 23.976), ("25/1", 25.0), ("0/0", 0.0), ("", 0.0), (None, 0.0), ("abc", 0.0)])
def test_parse_rate(value, expected):
    assert md._parse_rate(value) == expected


@pytest.mark.parametrize("stream, expected", [
    ({"side_data_list": [{"side_data_type": "DOVI configuration record"}], "color_transfer": "smpte2084"}, "DV"),
    ({"color_transfer": "smpte2084"}, "HDR10"),
    ({"color_transfer": "smpte2084", "side_data_list": [{"side_data_type": "HDR Dynamic Metadata SMPTE2094-40 (HDR10+)"}]}, "HDR10+"),
    ({"color_transfer": "arib-std-b67"}, "HLG"),
    ({"color_transfer": "bt709"}, "SDR"),
    ({}, "SDR"),
])
def test_hdr_range(stream, expected):
    assert md._hdr_range(stream) == expected


@pytest.mark.parametrize("stream, expected", [
    ({"bits_per_raw_sample": "10"}, 10),
    ({"pix_fmt": "yuv420p10le"}, 10),
    ({"pix_fmt": "p010le"}, 10),
    ({"pix_fmt": "yuv420p"}, 8),
    ({}, 0),
])
def test_bit_depth(stream, expected):
    assert md._bit_depth(stream) == expected


@pytest.mark.parametrize("stream, expected", [
    ({"channel_layout": "5.1(side)", "channels": 6}, "5.1"),
    ({"channel_layout": "7.1", "channels": 8}, "7.1"),
    ({"channel_layout": "stereo", "channels": 2}, "2.0"),
    ({"channels": 1}, "1.0"),
    ({"channels": 3}, "3.0"),
    ({}, ""),
])
def test_channels_label(stream, expected):
    assert md._channels_label(stream) == expected


def test_stats_bitrate_prefers_the_stream_then_mkvmerge_tags():
    assert md._stats_bitrate({"bit_rate": "5000"}) == 5000
    assert md._stats_bitrate({"tags": {"BPS-eng": "1234567"}}) == 1234567
    # NUMBER_OF_BYTES over DURATION: 1_000_000 bytes in 8 s = 1 Mbps
    assert md._stats_bitrate({"tags": {"NUMBER_OF_BYTES": "1000000", "DURATION": "00:00:08.000000000"}}) == 1_000_000
    assert md._stats_bitrate({"tags": {"DURATION": "00:00:08.000"}}) == 0
    assert md._stats_bitrate({}) == 0


def _run(monkeypatch, tmp_path, ffprobe_json, packets="", mkvmerge_json=None, name="media.mkv"):
    """get_media_metadata() with ffprobe / mkvmerge answers faked per command."""
    media = tmp_path / name
    media.write_bytes(b"x" * 1000)
    calls = []

    def fake_run(cmd, *_args, **_kwargs):
        calls.append(cmd[0])
        if cmd[0] == "mkvmerge":
            return SimpleNamespace(returncode=0, stdout=json.dumps(mkvmerge_json or {}), stderr="")
        if "-show_entries" in cmd:
            return SimpleNamespace(returncode=0, stdout=packets, stderr="")
        return SimpleNamespace(returncode=0, stdout=json.dumps(ffprobe_json), stderr="")

    monkeypatch.setattr(md, "get_ffprobe_path", lambda: "ffprobe")
    monkeypatch.setattr(md, "get_mkvmerge_path", lambda: "mkvmerge")
    monkeypatch.setattr(md.subprocess, "run", fake_run)
    return md.get_media_metadata.__wrapped__(str(media)), calls


VIDEO = {"codec_type": "video", "codec_name": "hevc", "width": 3840, "height": 2160, "avg_frame_rate": "24000/1001", "pix_fmt": "yuv420p10le",
         "color_transfer": "smpte2084", "profile": "Main 10", "level": 153, "display_aspect_ratio": "16:9", "field_order": "progressive",
         "duration": "10.0"}
AUDIO = {"codec_type": "audio", "codec_name": "eac3", "channels": 6, "channel_layout": "5.1(side)", "sample_rate": "48000", "tags": {"language": "ita"}}


def test_every_new_field_is_filled(monkeypatch, tmp_path):
    data, _ = _run(monkeypatch, tmp_path, {"streams": [{**VIDEO, "bit_rate": "4000000"}, AUDIO], "format": {"duration": "10.0", "size": "5000000", "format_name": "mov,mp4"}})

    assert (data["fps"], data["hdr"], data["bit_depth"]) == (23.976, "HDR10", 10)
    assert (data["video_bitrate"], data["video_bitrate_source"]) == (4_000_000, "stream")
    assert (data["video_profile"], data["video_level"], data["pix_fmt"]) == ("Main 10", 153, "yuv420p10le")
    assert (data["aspect_ratio"], data["field_order"], data["duration"], data["size"]) == ("16:9", "progressive", 10.0, 5_000_000)
    assert data["audio_channels"] == "5.1"
    assert data["audio_tracks"][0]["sample_rate"] == 48000 and data["audio_tracks"][0]["channel_layout"] == "5.1(side)"


def test_mkv_without_stats_sums_the_packets(monkeypatch, tmp_path):
    # ffmpeg-muxed mkv: no bit_rate, no BPS tag. 2 packets x 5_000_000 bytes in 10 s = 8 Mbps
    data, calls = _run(monkeypatch, tmp_path, {"streams": [VIDEO], "format": {"duration": "10.0", "bit_rate": "9999999", "format_name": "matroska,webm"}},
                       packets="5000000\n5000000\n", mkvmerge_json={"tracks": []})
    assert (data["video_bitrate"], data["video_bitrate_source"]) == (8_000_000, "packets")
    assert "mkvmerge" in calls


def test_mkvmerge_fills_fps_and_statistics_tags(monkeypatch, tmp_path):
    video = {k: v for k, v in VIDEO.items() if k != "avg_frame_rate"}
    mkv = {"tracks": [{"type": "video", "properties": {"default_duration": 41708333, "tag_bps": "3500000"}}]}
    data, _ = _run(monkeypatch, tmp_path, {"streams": [video], "format": {"duration": "10.0", "format_name": "matroska,webm"}}, mkvmerge_json=mkv)

    assert data["fps"] == 23.976
    assert (data["video_bitrate"], data["video_bitrate_source"]) == (3_500_000, "mkvmerge")


def test_mkvmerge_is_not_run_for_other_containers_or_when_ffprobe_has_everything(monkeypatch, tmp_path):
    _, calls = _run(monkeypatch, tmp_path, {"streams": [{**VIDEO, "bit_rate": "1"}], "format": {"format_name": "matroska,webm"}})
    assert "mkvmerge" not in calls
    _, calls = _run(monkeypatch, tmp_path, {"streams": [VIDEO], "format": {"duration": "10.0", "format_name": "mov,mp4"}}, packets="1000\n")
    assert "mkvmerge" not in calls


def test_container_bitrate_is_the_last_resort(monkeypatch, tmp_path):
    video = {k: v for k, v in VIDEO.items() if k != "duration"}
    data, _ = _run(monkeypatch, tmp_path, {"streams": [video], "format": {"bit_rate": "7000", "format_name": "mov,mp4"}})
    assert (data["video_bitrate"], data["video_bitrate_source"]) == (7000, "container")


def test_dolby_vision_side_data(monkeypatch, tmp_path):
    video = {**VIDEO, "bit_rate": "1", "side_data_list": [{"side_data_type": "DOVI configuration record"}]}
    data, _ = _run(monkeypatch, tmp_path, {"streams": [video], "format": {}})
    assert data["hdr"] == "DV"


def test_a_file_without_video_keeps_the_empty_defaults(monkeypatch, tmp_path):
    data, _ = _run(monkeypatch, tmp_path, {"streams": [AUDIO], "format": {}})
    assert (data["fps"], data["hdr"], data["video_bitrate"], data["bit_depth"]) == (0.0, "", 0, 0)
    assert data["audio_channels"] == "5.1"


# ── name tokens ──────────────────────────────────────────────────────────────

META = {"quality": "1080p", "video_codec": "H.265", "fps": 23.976, "video_bitrate": 1_211_865, "hdr": "HDR10", "bit_depth": 10, "audio_channels": "5.1"}


def test_token_values():
    values = media_token_values(META)
    assert values["fps"] == "23.976fps" and values["video_bitrate"] == "1212kbps"
    assert (values["hdr"], values["bit_depth"], values["audio_channels"]) == ("HDR10", "10bit", "5.1")
    assert media_token_values({**META, "fps": 25.0})["fps"] == "25fps"


def test_sdr_and_unknown_values_are_empty():
    values = media_token_values({"hdr": "SDR"})
    assert all(values[k] == "" for k in ("fps", "video_bitrate", "hdr", "bit_depth", "audio_channels"))


def test_resolve_media_tokens_fills_and_drops():
    root = "Show S01E01 [%(quality)] [%(hdr)] (%(fps)) [%(video_bitrate)] [%(bit_depth)] [%(audio_channels)]"
    assert resolve_media_tokens(root, META) == "Show S01E01 [1080p] [HDR10] (23.976fps) [1212kbps] [10bit] [5.1]"
    assert resolve_media_tokens(root, {"quality": "1080p", "hdr": "SDR"}) == "Show S01E01 [1080p]"


def test_unresolved_tokens_are_stripped_from_the_working_path():
    assert strip_media_tokens("Show [%(quality)] [%(hdr)] [%(fps)].mkv") == "Show.mkv"


def test_file_probe_keys_are_all_tokens():
    values = media_token_values({})
    assert set(FILE_PROBE_KEYS) <= set(values)
