# 21.10.26

"""ASS font embedding: which fonts an ASS needs, where they are found, and how both muxers get them attached."""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path, get_mkvmerge_path
from VibraVid.core.muxing import merge
from VibraVid.core.muxing.helper import font_embed
from VibraVid.core.muxing.helper.font import FontFace, parse_face_name
from VibraVid.core.muxing.helper.font_embed import (
    FontAttachment,
    ass_font_usage,
    collect_font_attachments,
    ffmpeg_attach_args,
    mkv_subtitle_codec_args,
    mkvmerge_attach_args,
    resolve_font_file,
)

STYLE_HEADER = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n"
    "[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
    "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
    "Alignment, MarginL, MarginR, MarginV, Encoding\n"
)
EVENTS_HEADER = (
    "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
)


def _style(name, font, bold=0, italic=0):
    return f"Style: {name},{font},40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,{bold},{italic},0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"


def write_ass(path, fonts=(("TestFont", 0, 0),), text="Hello", newline="\n", bom=False):
    body = STYLE_HEADER + "".join(_style(f"S{i}", f, b, it) for i, (f, b, it) in enumerate(fonts))
    body += EVENTS_HEADER + f"Dialogue: 0,0:00:00.00,0:00:02.00,S0,,0,0,0,,{text}\n"
    body = body.replace("\n", newline)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8"))
    return str(path)


@pytest.fixture
def font_downloads():
    """Pool filenames the code under test tried to download."""
    return []


@pytest.fixture
def fonts_dir(tmp_path, monkeypatch, font_downloads):
    """Hermetic font world: only the files in this folder exist, nothing is installed on the 'system' and nothing is downloaded."""
    folder = tmp_path / "fonts"
    folder.mkdir()
    monkeypatch.setattr(font_embed, "user_fonts_dir", lambda: str(folder))
    monkeypatch.setattr(font_embed, "pool_fonts_dir", lambda: str(tmp_path / "pool"))
    monkeypatch.setattr(font_embed, "_system_font_manager", SimpleNamespace(get_font_faces=lambda: []))
    monkeypatch.setattr(font_embed, "embed_fonts_enabled", lambda: True)
    monkeypatch.setattr(font_embed, "_pool_unreachable", False)
    monkeypatch.setattr(font_embed, "_failed_downloads", set())

    def fake_download(filename):
        font_downloads.append(filename)
        return None

    monkeypatch.setattr(font_embed, "_download_pool_font", fake_download)
    return folder


def put_font(folder, name, payload=b"font-bytes"):
    (folder / name).write_bytes(payload)
    return str(folder / name)


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Arial", ("arial", False, False)),
        ("Arial Bold", ("arial", True, False)),
        ("Arial Bold Italic", ("arial", True, True)),
        ("DejaVu Sans Bold Oblique", ("dejavu sans", True, True)),
        ("Trebuchet MS Italic", ("trebuchet ms", False, True)),
        ("Arial Black", ("arial black", False, False)),
        ("Rubik Medium Italic", ("rubik medium", False, True)),
        ("Bold", ("bold", False, False)),
    ],
)
def test_parse_face_name(name, expected):
    assert parse_face_name(name) == expected


def test_style_fontname_with_bold_and_italic_flags(tmp_path):
    path = write_ass(tmp_path / "a.ass", fonts=(("Trebuchet MS", 0, 0), ("Arial", -1, 0), ("Georgia", 0, -1)))
    assert ass_font_usage(path) == [("Trebuchet MS", False, False), ("Arial", True, False), ("Georgia", False, True)]


def test_vertical_font_prefix_is_stripped_and_duplicates_collapse(tmp_path):
    path = write_ass(tmp_path / "a.ass", fonts=(("@MS Gothic", 0, 0), ("ms gothic", 0, 0)))
    assert ass_font_usage(path) == [("MS Gothic", False, False)]


def test_fn_override_tags_are_collected(tmp_path):
    path = write_ass(tmp_path / "a.ass", text=r"{\fnComic Sans MS\b0}Hi {\fnImpact}there{\fn}")
    names = {name for name, _b, _i in ass_font_usage(path)}
    assert names == {"TestFont", "Comic Sans MS", "Impact"}


def test_bold_and_italic_overrides_add_the_variants(tmp_path):
    path = write_ass(tmp_path / "a.ass", text=r"{\b1}Bold{\b0} {\i1}Italic")
    usage = set(ass_font_usage(path))
    assert usage == {
        ("TestFont", False, False),
        ("TestFont", True, False),
        ("TestFont", False, True),
        ("TestFont", True, True),
    }


def test_bold_override_lookalikes_do_not_trigger(tmp_path):
    path = write_ass(tmp_path / "a.ass", text=r"{\blur2\bord3\be1}Hi")
    assert ass_font_usage(path) == [("TestFont", False, False)]


def test_bom_and_crlf_files_parse(tmp_path):
    path = write_ass(tmp_path / "a.ass", fonts=(("Verdana", -1, 0),), newline="\r\n", bom=True)
    assert ass_font_usage(path) == [("Verdana", True, False)]


def test_unreadable_or_style_less_files_yield_nothing(tmp_path):
    assert ass_font_usage(str(tmp_path / "missing.ass")) == []
    (tmp_path / "e.ass").write_text("[Script Info]\nTitle: x\n", encoding="utf-8")
    assert ass_font_usage(str(tmp_path / "e.ass")) == []


# ---------------------------------------------------------------- resolution


def test_exact_face_wins_over_regular(fonts_dir):
    regular = put_font(fonts_dir, "TestFont.ttf")
    bold = put_font(fonts_dir, "TestFont-Bold.ttf")
    faces = font_embed._user_faces()
    assert resolve_font_file("TestFont", False, False, faces) == regular
    assert resolve_font_file("TestFont", True, False, faces) == bold
    assert resolve_font_file("testfont bold", False, False, faces) == bold


def test_missing_variant_falls_back_to_regular(fonts_dir):
    regular = put_font(fonts_dir, "TestFont.ttf")
    assert resolve_font_file("TestFont", True, True, font_embed._user_faces()) == regular


def test_unknown_family_is_none_and_never_downloads_non_pool_fonts(fonts_dir, font_downloads):
    assert resolve_font_file("Nonexistent Font", False, False, font_embed._user_faces()) is None
    assert font_downloads == []


def test_pool_font_is_fetched_when_not_found_locally(fonts_dir, font_downloads):
    resolve_font_file("Trebuchet MS", True, False, [])
    assert font_downloads[0] == "trebucbd.ttf"


def test_pool_regular_is_the_last_resort_when_the_variant_cannot_be_fetched(fonts_dir, monkeypatch):
    asked = []

    def fake_download(filename):
        asked.append(filename)
        return "/cache/" + filename if filename == "trebuc.ttf" else None

    monkeypatch.setattr(font_embed, "_download_pool_font", fake_download)
    assert resolve_font_file("Trebuchet MS", True, False, []) == "/cache/trebuc.ttf"
    assert asked == ["trebucbd.ttf", "trebuc.ttf"]


def test_pool_font_is_tried_before_the_system_face(fonts_dir, font_downloads, monkeypatch):
    faces = [FontFace("trebuchet ms", False, False, "/sys/trebuc.ttf")]
    monkeypatch.setattr(font_embed, "_download_pool_font", lambda filename: font_downloads.append(filename) or "/pool/" + filename)
    assert resolve_font_file("Trebuchet MS", False, False, faces) == "/pool/trebuc.ttf"
    assert font_downloads == ["trebuc.ttf"]


def test_downloaded_pool_font_beats_the_system_face_without_new_requests(fonts_dir, font_downloads, tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    cached = put_font(pool, "trebuc.ttf")
    faces = font_embed._user_faces() + [FontFace("trebuchet ms", False, False, "/sys/trebuc.ttf")]
    assert resolve_font_file("Trebuchet MS", False, False, faces) == cached
    assert font_downloads == []


def test_system_face_is_the_fallback_when_the_pool_cannot_be_reached(fonts_dir, font_downloads):
    faces = [FontFace("trebuchet ms", False, False, "/sys/trebuc.ttf")]
    assert resolve_font_file("Trebuchet MS", False, False, faces) == "/sys/trebuc.ttf"
    assert font_downloads == ["trebuc.ttf"]


def test_pool_server_error_stops_further_downloads_for_the_run(monkeypatch, tmp_path):
    monkeypatch.setattr(font_embed, "config_manager", SimpleNamespace(base_path=str(tmp_path)))
    monkeypatch.setattr(font_embed, "_pool_unreachable", False)
    monkeypatch.setattr(font_embed, "_failed_downloads", set())
    requests = []

    class Client:
        def get(self, url):
            requests.append(url)
            return SimpleNamespace(status_code=502, content=b"<html>error</html>")

        def close(self):
            pass

    monkeypatch.setattr("VibraVid.utils.http_client.create_client", lambda **kwargs: Client())
    assert font_embed._download_pool_font("trebuc.ttf") is None
    assert font_embed._download_pool_font("arial.ttf") is None
    assert len(requests) == 1
    assert not (tmp_path / ".cache" / "services" / "crunchyroll" / "fonts" / "arial.ttf").exists()


def test_a_missing_font_does_not_mark_the_pool_unreachable(monkeypatch, tmp_path):
    monkeypatch.setattr(font_embed, "config_manager", SimpleNamespace(base_path=str(tmp_path)))
    monkeypatch.setattr(font_embed, "_pool_unreachable", False)
    monkeypatch.setattr(font_embed, "_failed_downloads", set())
    answers = {"gone.ttf": SimpleNamespace(status_code=404, content=b""), "arial.ttf": SimpleNamespace(status_code=200, content=b"ttf")}

    class Client:
        def get(self, url):
            return answers[url.rsplit("/", 1)[-1]]

        def close(self):
            pass

    monkeypatch.setattr("VibraVid.utils.http_client.create_client", lambda **kwargs: Client())
    assert font_embed._download_pool_font("gone.ttf") is None
    assert font_embed._download_pool_font("arial.ttf") is not None


def test_downloaded_pool_files_keep_their_published_face_name(fonts_dir):
    put_font(fonts_dir, "trebucbd.ttf")
    put_font(fonts_dir, "AdobeArabic-Bold.otf")
    faces = {(f.family, f.bold, f.italic) for f in font_embed._user_faces()}
    assert ("trebuchet ms", True, False) in faces
    assert ("adobe arabic", False, False) in faces  # the pool serves its only (bold) file for the plain family


def test_pool_fonts_are_downloaded_into_the_crunchyroll_service_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(font_embed, "config_manager", SimpleNamespace(base_path=str(tmp_path)))
    expected = tmp_path / ".cache" / "services" / "crunchyroll" / "fonts"
    assert font_embed.pool_fonts_dir() == str(expected)

    class Client:
        def get(self, url):
            assert url == font_embed.FALLBACK_POOL_URL + "trebuc.ttf"
            return SimpleNamespace(status_code=200, content=b"ttf-bytes")

        def close(self):
            pass

    monkeypatch.setattr("VibraVid.utils.http_client.create_client", lambda **kwargs: Client())
    path = font_embed._download_pool_font("trebuc.ttf")
    assert path == str(expected / "trebuc.ttf")
    assert (expected / "trebuc.ttf").read_bytes() == b"ttf-bytes"
    assert [(f.family, f.bold, f.italic) for f in font_embed._user_faces()] == [("trebuchet ms", False, False)]


# ---------------------------------------------------------------- collect_font_attachments


def test_non_matroska_outputs_get_no_fonts(fonts_dir, tmp_path):
    put_font(fonts_dir, "TestFont.ttf")
    sub = write_ass(tmp_path / "a.ass")
    for ext in (".mp4", ".m4v", ".ts", ""):
        assert collect_font_attachments([sub], str(tmp_path / f"out{ext}")) == []


def test_switch_off_disables_embedding(fonts_dir, tmp_path, monkeypatch):
    put_font(fonts_dir, "TestFont.ttf")
    sub = write_ass(tmp_path / "a.ass")
    monkeypatch.setattr(font_embed, "embed_fonts_enabled", lambda: False)
    assert collect_font_attachments([sub], str(tmp_path / "out.mkv")) == []


def test_embed_fonts_flag_is_read_from_process_section_defaulting_to_on(monkeypatch):
    class Config:
        def __init__(self, data):
            self.data = data

        def get_bool(self, section, key, default=None):
            return bool(self.data.get(section, {}).get(key, default))

    for data, expected in (({}, True), ({"PROCESS": {"embed_fonts": False}}, False), ({"PROCESS": {"embed_fonts": True}}, True)):
        monkeypatch.setattr(font_embed, "config_manager", SimpleNamespace(config=Config(data), base_path="."))
        assert font_embed.embed_fonts_enabled() is expected


def test_only_ass_and_ssa_tracks_contribute(fonts_dir, tmp_path):
    put_font(fonts_dir, "TestFont.ttf")
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    vtt = tmp_path / "a.vtt"
    vtt.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nHi\n", encoding="utf-8")
    assert collect_font_attachments([str(srt), str(vtt)], str(tmp_path / "out.mkv")) == []

    ssa = write_ass(tmp_path / "b.ssa")
    attachments = collect_font_attachments([str(srt), ssa], str(tmp_path / "out.mkv"))
    assert [a.name for a in attachments] == ["TestFont.ttf"]


def test_forced_cc_and_sdh_tracks_all_get_their_fonts_once(fonts_dir, tmp_path):
    put_font(fonts_dir, "TestFont.ttf")
    put_font(fonts_dir, "ForcedFont.ttf")
    put_font(fonts_dir, "CcFont.otf")
    normal = write_ass(tmp_path / "it.ass", fonts=(("TestFont", 0, 0),))
    forced = write_ass(tmp_path / "it_forced.ass", fonts=(("ForcedFont", 0, 0), ("TestFont", 0, 0)))
    cc = write_ass(tmp_path / "en_cc.ass", fonts=(("CcFont", 0, 0),))
    sdh = write_ass(tmp_path / "en_sdh.ass", fonts=(("CcFont", 0, 0), ("TestFont", 0, 0)))

    attachments = collect_font_attachments([normal, forced, cc, sdh], str(tmp_path / "out.mkv"))

    assert sorted(a.name for a in attachments) == ["CcFont.otf", "ForcedFont.ttf", "TestFont.ttf"]
    mimes = {a.name: a.mime for a in attachments}
    assert mimes["CcFont.otf"] == "application/vnd.ms-opentype"
    assert mimes["TestFont.ttf"] == "application/x-truetype-font"


def test_missing_fonts_are_reported_but_do_not_abort(fonts_dir, tmp_path, printed):
    put_font(fonts_dir, "TestFont.ttf")
    sub = write_ass(tmp_path / "a.ass", fonts=(("TestFont", 0, 0), ("Totally Unknown", 0, 0)))
    attachments = collect_font_attachments([sub], str(tmp_path / "out.mkv"))
    assert [a.name for a in attachments] == ["TestFont.ttf"]
    assert "Totally Unknown" in printed()


def test_empty_font_files_are_skipped(fonts_dir, tmp_path):
    put_font(fonts_dir, "TestFont.ttf", payload=b"")
    sub = write_ass(tmp_path / "a.ass")
    assert collect_font_attachments([sub], str(tmp_path / "out.mkv")) == []


def test_oversized_font_files_are_skipped(fonts_dir, tmp_path, monkeypatch):
    put_font(fonts_dir, "TestFont.ttf", payload=b"x" * 64)
    monkeypatch.setattr(font_embed, "MAX_FONT_BYTES", 10)
    assert collect_font_attachments([write_ass(tmp_path / "a.ass")], str(tmp_path / "out.mkv")) == []


# ---------------------------------------------------------------- argument builders


def test_ffmpeg_attach_args_tag_each_attachment_stream():
    atts = [FontAttachment("a.ttf", "/f/a.ttf", "application/x-truetype-font"), FontAttachment("b.otf", "/f/b.otf", "application/vnd.ms-opentype")]
    assert ffmpeg_attach_args(atts) == [
        "-attach", "/f/a.ttf", "-metadata:s:t:0", "mimetype=application/x-truetype-font", "-metadata:s:t:0", "filename=a.ttf",
        "-attach", "/f/b.otf", "-metadata:s:t:1", "mimetype=application/vnd.ms-opentype", "-metadata:s:t:1", "filename=b.otf",
    ]  # fmt: skip
    assert ffmpeg_attach_args(atts[:1], first_index=3)[2] == "-metadata:s:t:3"
    assert ffmpeg_attach_args([]) == []


def test_mkvmerge_attach_args():
    atts = [FontAttachment("a.ttf", "/f/a.ttf", "application/x-truetype-font")]
    assert mkvmerge_attach_args(atts) == [
        "--attachment-name", "a.ttf", "--attachment-mime-type", "application/x-truetype-font", "--attach-file", "/f/a.ttf",
    ]  # fmt: skip
    assert mkvmerge_attach_args([]) == []


def test_mkv_subtitle_codec_args_keep_ass_and_flatten_the_rest():
    assert mkv_subtitle_codec_args(["a.ass", "b.SSA", "c.srt", "d.vtt"]) == [
        "-c:s:0", "ass", "-c:s:1", "ass", "-c:s:2", "srt", "-c:s:3", "srt",
    ]  # fmt: skip
    assert mkv_subtitle_codec_args([]) == []


# ---------------------------------------------------------------- the muxer commands


@pytest.fixture
def commands(monkeypatch):
    """Capture the ffmpeg / mkvmerge command lines instead of running them."""
    captured = {}

    def fake_capture(cmd, label, duration, **kwargs):
        captured["cmd"] = list(cmd)
        return {"exit_code": 0}

    monkeypatch.setattr(merge, "capture_ffmpeg_real_time", fake_capture)
    monkeypatch.setattr(merge, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(merge, "get_mkvmerge_path", lambda: "mkvmerge")
    monkeypatch.setattr(merge, "get_video_duration", lambda path: 10.0)
    monkeypatch.setattr(merge, "is_mpegts_file", lambda path: False)
    monkeypatch.setattr(merge, "detect_ts_timestamp_issues", lambda path: False)
    monkeypatch.setattr(merge, "has_audio", lambda path: True)
    monkeypatch.setattr(merge, "_strip_drm_boxes", lambda path: path)
    monkeypatch.setattr(merge, "USE_GPU", False)
    monkeypatch.setattr(merge, "EXTRACT_EMBEDDED_CC", False)
    monkeypatch.setattr(merge, "_build_global_metadata_flags", lambda: [])
    monkeypatch.setattr(merge, "_maybe_tag_hevc_for_mkv", lambda *a, **k: None)
    monkeypatch.setattr(merge, "add_encoding_params", lambda cmd: cmd.extend(["-c", "copy"]))
    monkeypatch.setattr(merge, "SUBTITLE_DISPOSITION_LANGUAGE", "")
    return captured


@pytest.fixture
def mixed_tracks(fonts_dir, tmp_path):
    put_font(fonts_dir, "TestFont.ttf")
    put_font(fonts_dir, "CcFont.ttf")
    srt = tmp_path / "plain.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    return [
        {"path": write_ass(tmp_path / "it.ass"), "language": "it-it"},
        {"path": write_ass(tmp_path / "it_forced.ass"), "language": "it-it_forced", "forced": True},
        {"path": write_ass(tmp_path / "en_cc.ass", fonts=(("CcFont", 0, 0),)), "language": "en-us_cc", "cc": True},
        {"path": str(srt), "language": "fr-fr"},
    ]


def _pairs(cmd, flag):
    return [cmd[i + 1] for i, token in enumerate(cmd) if token == flag]


def test_ffmpeg_mkv_keeps_ass_per_stream_and_never_sets_a_generic_subtitle_codec(commands, mixed_tracks, tmp_path):
    out = str(tmp_path / "out.mkv")
    merge._join_media_ffmpeg("video.mp4", [], mixed_tracks, out, False)
    cmd = commands["cmd"]

    assert "-c:s" not in cmd  # a trailing generic `-c:s srt` would override the per-stream ASS and flatten the styling
    assert cmd.index("-c:s:0") > cmd.index("copy")  # ...and the encoding params (`-c copy`) must not come after them either
    assert [cmd[i + 1] for i, t in enumerate(cmd) if t.startswith("-c:s:")] == ["ass", "ass", "ass", "srt"]
    assert sorted(_pairs(cmd, "-attach")) == sorted(
        [str(tmp_path / "fonts" / "TestFont.ttf"), str(tmp_path / "fonts" / "CcFont.ttf")]
    )
    assert cmd.index("-attach") < cmd.index(out)
    assert "mimetype=application/x-truetype-font" in cmd


def test_ffmpeg_mkv_forced_and_cc_dispositions_survive_embedding(commands, mixed_tracks, tmp_path):
    merge._join_media_ffmpeg("video.mp4", [], mixed_tracks, str(tmp_path / "out.mkv"), False)
    cmd = commands["cmd"]
    dispositions = {cmd[i]: cmd[i + 1] for i, t in enumerate(cmd) if t.startswith("-disposition:s:") and cmd[i + 1] != "0"}

    assert dispositions["-disposition:s:1"] == "forced"
    assert dispositions["-disposition:s:2"] == "hearing_impaired"
    assert "-disposition:s:0" not in dispositions and "-disposition:s:3" not in dispositions


def test_ffmpeg_mp4_never_gets_fonts_and_uses_mov_text(commands, mixed_tracks, tmp_path):
    merge._join_media_ffmpeg("video.mp4", [], mixed_tracks, str(tmp_path / "out.mp4"), False)
    cmd = commands["cmd"]
    assert "-attach" not in cmd
    assert [cmd[i + 1] for i, t in enumerate(cmd) if t.startswith("-c:s:")] == ["mov_text"] * 4


def test_ffmpeg_mkv_without_ass_has_no_attachments(commands, fonts_dir, tmp_path):
    srt = tmp_path / "plain.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    merge._join_media_ffmpeg("video.mp4", [], [{"path": str(srt), "language": "en-us"}], str(tmp_path / "out.mkv"), False)
    assert "-attach" not in commands["cmd"]


def test_mkvmerge_attaches_fonts_and_keeps_forced_cc_flags(commands, mixed_tracks, tmp_path):
    out = str(tmp_path / "out.mkv")
    merge._join_media_mkvmerge("video.mp4", [], mixed_tracks, out)
    cmd = commands["cmd"]

    assert sorted(_pairs(cmd, "--attach-file")) == sorted(
        [str(tmp_path / "fonts" / "TestFont.ttf"), str(tmp_path / "fonts" / "CcFont.ttf")]
    )
    assert _pairs(cmd, "--attachment-mime-type") == ["application/x-truetype-font"] * 2
    assert _pairs(cmd, "--forced-track") == ["0:no", "0:yes", "0:no", "0:no"]
    assert _pairs(cmd, "--hearing-impaired-flag") == ["0:no", "0:no", "0:yes", "0:no"]
    assert cmd[cmd.index("--no-attachments") - 1] != "--attach-file"  # the video's own attachments stay dropped


def test_mkvmerge_without_ass_has_no_attachments(commands, fonts_dir, tmp_path):
    srt = tmp_path / "plain.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    merge._join_media_mkvmerge("video.mp4", [], [{"path": str(srt), "language": "en-us"}], str(tmp_path / "out.mkv"))
    assert "--attach-file" not in commands["cmd"]


# ---------------------------------------------------------------- real tools


def _tool(getter):
    try:
        path = getter()
    except Exception:
        return None
    return path if path and os.path.exists(path) else None


FFMPEG = _tool(get_ffmpeg_path)
FFPROBE = _tool(get_ffprobe_path)
MKVMERGE = _tool(get_mkvmerge_path)
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not available")
needs_mkvmerge = pytest.mark.skipif(not (FFMPEG and FFPROBE and MKVMERGE), reason="ffmpeg/ffprobe/mkvmerge not available")


def _probe(path):
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-print_format", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(result.stdout)["streams"]


def _source_video(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x120:d=2", str(video)], check=True)
    return str(video)


def _check_result(path):
    streams = _probe(path)
    subs = [s for s in streams if s["codec_type"] == "subtitle"]
    fonts = [s for s in streams if s["codec_type"] == "attachment"]
    assert [s["codec_name"] for s in subs] == ["ass", "ass", "ass", "subrip"]
    assert sorted(s["tags"]["filename"] for s in fonts) == ["CcFont.ttf", "TestFont.ttf"]
    assert subs[1]["disposition"]["forced"] == 1
    assert subs[0]["disposition"]["forced"] == 0
    return streams


@needs_ffmpeg
def test_real_ffmpeg_mux_keeps_ass_forced_cc_and_embeds_fonts(mixed_tracks, tmp_path):
    out = str(tmp_path / "out.mkv")
    merge._join_media_ffmpeg(_source_video(tmp_path), [], mixed_tracks, out, False)
    streams = _check_result(out)
    assert [s for s in streams if s["codec_type"] == "subtitle"][2]["disposition"]["hearing_impaired"] == 1


@needs_mkvmerge
def test_real_mkvmerge_mux_keeps_ass_forced_cc_and_embeds_fonts(mixed_tracks, tmp_path):
    out = str(tmp_path / "out.mkv")
    merge._join_media_mkvmerge(_source_video(tmp_path), [], mixed_tracks, out)
    streams = _check_result(out)
    assert [s for s in streams if s["codec_type"] == "subtitle"][2]["disposition"]["hearing_impaired"] == 1
