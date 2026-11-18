# 21.10.26

"""``embed_poster`` on/off, and the whole subtitle -> fonts -> cover pipeline end to end with real ffmpeg / mkvmerge."""

import hashlib
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from VibraVid.core.downloader.base import BaseDownloader
from VibraVid.core.downloader._generic import Generic_Downloader
from VibraVid.core.muxing import merge
from VibraVid.core.muxing.helper import font_embed
from VibraVid.services._base import tmdb_artwork
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path, get_mkvmerge_path

_ASS_HEAD = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n[V4+ Styles]\n"
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
    "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
)


def write_ass(path, fonts):
    styles = "".join(
        f"Style: S{i},{name},40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,{bold},0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n"
        for i, (name, bold, _italic) in enumerate(fonts)
    )
    events = "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    events += "Dialogue: 0,0:00:00.00,0:00:01.50,S0,,0,0,0,,{\\b1}Hello\n"
    path.write_text(_ASS_HEAD + styles + events, encoding="utf-8")
    return str(path)


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
needs_both = pytest.mark.skipif(not (FFMPEG and FFPROBE and MKVMERGE), reason="ffmpeg/ffprobe/mkvmerge not available")
ENGINES = [pytest.param("ffmpeg", marks=needs_ffmpeg), pytest.param("mkvmerge", marks=needs_both)]


class _Config:
    def __init__(self, data):
        self.data = data

    def get_bool(self, section, key, default=None):
        try:
            return bool(self.data[section][key])
        except KeyError:
            if default is None:
                raise ValueError(f"{section}.{key} missing") from None
            return default


def _set_embed_poster(monkeypatch, value):
    data = {} if value is None else {"DOWNLOAD": {"embed_poster": value}}
    monkeypatch.setattr(tmdb_artwork, "config_manager", SimpleNamespace(config=_Config(data)))


# ---------------------------------------------------------------- the flag itself


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), (None, True)])
def test_embed_enabled_reads_download_embed_poster(monkeypatch, value, expected):
    _set_embed_poster(monkeypatch, value)
    assert tmdb_artwork.embed_enabled() is expected


def _downloader(poster="https://img/x.jpg"):
    return SimpleNamespace(poster_url=poster, last_merge_result=None)


def test_embed_poster_false_never_touches_the_file(monkeypatch):
    _set_embed_poster(monkeypatch, False)
    calls = []
    monkeypatch.setattr("VibraVid.core.downloader.base.embed_poster", lambda *a, **k: calls.append(a) or ("changed.mkv", {"exit_code": 0}))

    assert BaseDownloader._embed_poster(_downloader(), "movie.mkv") == "movie.mkv"
    assert calls == []


def test_embed_poster_true_runs_the_embedder_with_the_poster_url(monkeypatch):
    _set_embed_poster(monkeypatch, True)
    calls = []

    def fake(path, url):
        calls.append((path, url))
        return "movie_poster.mkv", {"exit_code": 0}

    monkeypatch.setattr("VibraVid.core.downloader.base.embed_poster", fake)
    downloader = _downloader()

    assert BaseDownloader._embed_poster(downloader, "movie.mkv") == "movie_poster.mkv"
    assert calls == [("movie.mkv", "https://img/x.jpg")]
    assert downloader.last_merge_result == {"exit_code": 0}


def test_embed_poster_true_without_a_poster_url_is_a_noop(tmp_path):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"data")
    assert merge.embed_poster(str(path), None) == (str(path), {})
    assert path.read_bytes() == b"data"


def test_embed_poster_ignores_containers_that_cannot_carry_one(monkeypatch, tmp_path):
    monkeypatch.setattr(merge, "get_cached_tmdb_image", lambda url: b"jpeg")
    path = tmp_path / "movie.ts"
    path.write_bytes(b"data")
    assert merge.embed_poster(str(path), "https://img/x.jpg") == (str(path), {})


@pytest.fixture
def poster_command(monkeypatch, tmp_path):
    captured = {}

    def fake_capture(cmd, label, duration, **kwargs):
        captured["cmd"] = list(cmd)
        return {"exit_code": 0}

    monkeypatch.setattr(merge, "capture_ffmpeg_real_time", fake_capture)
    monkeypatch.setattr(merge, "get_cached_tmdb_image", lambda url: b"\xff\xd8jpeg")
    monkeypatch.setattr(merge, "get_ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(merge, "get_mkvmerge_path", lambda: "mkvmerge")
    monkeypatch.setattr(merge, "get_video_duration", lambda path: 10.0)
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"data")
    return captured, str(path)


@pytest.mark.parametrize("existing,index", [(0, 0), (2, 2), (5, 5)])
def test_ffmpeg_poster_tags_only_the_new_attachment(monkeypatch, poster_command, existing, index):
    captured, path = poster_command
    monkeypatch.setattr(merge, "MUX_ENGINE", "ffmpeg")
    monkeypatch.setattr(merge, "get_stream_codecs", lambda p: [{"codec_type": "video"}] + [{"codec_type": "attachment"}] * existing)

    merge.embed_poster(path, "https://img/x.jpg")
    cmd = captured["cmd"]

    assert f"-metadata:s:t:{index}" in cmd and "-metadata:s:t" not in cmd  # never the all-attachments specifier (it would rename the fonts)
    assert cmd[cmd.index("-attach") + 1].endswith("cover.jpg")
    assert cmd[cmd.index("-map") + 1] == "0"


def test_mkvmerge_poster_attaches_cover_without_dropping_existing_attachments(monkeypatch, poster_command):
    captured, path = poster_command
    monkeypatch.setattr(merge, "MUX_ENGINE", "mkvmerge")

    merge.embed_poster(path, "https://img/x.jpg")
    cmd = captured["cmd"]

    assert cmd[cmd.index("--attach-file") + 1].endswith("cover.jpg")
    assert "--no-attachments" not in cmd
    assert cmd[cmd.index("--attachment-name") + 1] == "cover.jpg"


# ---------------------------------------------------------------- real tools


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Fonts, subtitles of every kind (normal / forced / CC / SDH, ASS + VTT) and a cover, with downloads disabled."""
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in ("TestFont.ttf", "ForcedFont.ttf", "CcFont.otf", "Arial.ttf"):
        (fonts / name).write_bytes(b"fake-font-" + name.encode())

    monkeypatch.setattr(font_embed, "user_fonts_dir", lambda: str(fonts))
    monkeypatch.setattr(font_embed, "pool_fonts_dir", lambda: str(tmp_path / "pool"))
    monkeypatch.setattr(font_embed, "_system_font_manager", SimpleNamespace(get_font_faces=lambda: []))
    monkeypatch.setattr(font_embed, "embed_fonts_enabled", lambda: True)
    monkeypatch.setattr(font_embed, "_download_pool_font", lambda filename: None)
    monkeypatch.setattr(merge, "FORCE_SUBTITLE", "ass")
    monkeypatch.setattr(merge, "SUBTITLE_DISPOSITION_LANGUAGE", "")

    vtt = tmp_path / "fr_cc.vtt"
    vtt.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nBonjour\n", encoding="utf-8")
    subs = [
        {"path": write_ass(tmp_path / "it.ass", fonts=(("TestFont", 0, 0),)), "language": "it-it"},
        {"path": write_ass(tmp_path / "it_forced.ass", fonts=(("ForcedFont", 0, 0),)), "language": "it-it_forced", "forced": True},
        {"path": write_ass(tmp_path / "en_cc.ass", fonts=(("CcFont", 0, 0),)), "language": "en-us_cc", "cc": True},
        {"path": write_ass(tmp_path / "en_sdh.ass", fonts=(("TestFont", -1, 0),)), "language": "en-us_sdh", "sdh": True},
        {"path": str(vtt), "language": "fr-fr_cc", "cc": True},
    ]

    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x120:d=2", str(tmp_path / "v.mp4")], check=True)
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1", str(tmp_path / "cover.jpg")], check=True)
    cover = (tmp_path / "cover.jpg").read_bytes()
    monkeypatch.setattr(merge, "get_cached_tmdb_image", lambda url: cover)
    return SimpleNamespace(video=str(tmp_path / "v.mp4"), subs=subs, tmp=tmp_path)


def _probe(path):
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-print_format", "json", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return json.loads(result.stdout)["streams"]


def _digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _summary(path):
    streams = _probe(path)
    subs = [s for s in streams if s["codec_type"] == "subtitle"]
    attachments = sorted(s["tags"]["filename"] for s in streams if s["codec_type"] == "attachment")
    cover = any(s.get("tags", {}).get("filename") == "cover.jpg" for s in streams)
    return subs, attachments, cover


@pytest.mark.parametrize("engine", ENGINES)
def test_join_media_end_to_end_with_all_subtitle_kinds(world, monkeypatch, engine):
    monkeypatch.setattr(merge, "MUX_ENGINE", engine)
    out, result = merge.join_media(world.video, [], world.subs, str(world.tmp / "out.mkv"))

    assert not result.get("exit_code")
    subs, attachments, cover = _summary(out)

    # every track stays ASS (the VTT was converted to ASS by force_subtitle="ass"), nothing is flattened to SRT
    assert [s["codec_name"] for s in subs] == ["ass"] * 5
    assert subs[1]["disposition"]["forced"] == 1
    assert subs[2]["disposition"]["hearing_impaired"] == 1
    assert subs[3]["disposition"]["hearing_impaired"] == 1
    assert subs[4]["disposition"]["hearing_impaired"] == 1
    assert subs[0]["disposition"]["forced"] == 0 and subs[0]["disposition"]["hearing_impaired"] == 0

    # the same titles and languages whichever engine muxed them
    assert [s["tags"]["title"] for s in subs] == ["Italian", "Italian [Forced]", "English [CC]", "English [SDH]", "French [CC]"]
    # Matroska keeps the bibliographic code for French (mkvmerge writes "fre", ffmpeg "fra")
    assert ["fra" if s["tags"]["language"] == "fre" else s["tags"]["language"] for s in subs] == ["ita", "ita", "eng", "eng", "fra"]

    # TestFont (regular + the bold override), ForcedFont, CcFont, plus Arial that ffmpeg writes when it converts the VTT
    assert "TestFont.ttf" in attachments and "ForcedFont.ttf" in attachments and "CcFont.otf" in attachments and "Arial.ttf" in attachments
    assert len(attachments) == len(set(attachments))
    assert not cover


SUBTITLES_ENG_FIRST = ["English [CC]", "English [SDH]", "Italian", "Italian [Forced]", "French [CC]"]


@pytest.mark.parametrize("engine", ENGINES)
def test_join_media_writes_the_subtitles_in_the_order_select_subtitle_gave_them(world, monkeypatch, engine):
    from VibraVid.core.utils.track_order import order_tracks

    monkeypatch.setattr(merge, "MUX_ENGINE", engine)
    out, result = merge.join_media(world.video, [], order_tracks(world.subs, "subtitle", "eng|ita"), str(world.tmp / "out.mkv"))

    assert not result.get("exit_code")
    subs, _attachments, _cover = _summary(out)
    assert [s["tags"]["title"] for s in subs] == SUBTITLES_ENG_FIRST


@needs_ffmpeg
def test_streaming_direct_fast_path_orders_subtitles_by_select_subtitle(world, monkeypatch):
    """Same order from the select_subtitle of the download: "eng|ita", or the slots "1eng|2ita"."""
    from VibraVid.core.downloader import _generic

    monkeypatch.setattr(_generic, "get_ffmpeg_path", lambda: FFMPEG)
    fragmented = world.tmp / "frag.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", world.video, "-c", "copy", "-movflags", "frag_keyframe+empty_moov", str(fragmented)], check=True)
    entries = [{"label": f"s{i}", "source": {"role": "subtitle"}, "result": {"path": sub["path"], "language": sub["language"]}} for i, sub in enumerate(world.subs)]

    for n, select_subtitle in enumerate(("eng|ita", "1eng|2ita")):
        fake = SimpleNamespace(output_path=str(world.tmp / f"live{n}.mkv"), custom_filters={"subtitle": select_subtitle})
        cmd, output_path = Generic_Downloader._build_streaming_mux_cmd_direct(fake, {"source": {"codecs": "avc1"}}, entries)
        run = subprocess.run(cmd, input=fragmented.read_bytes(), capture_output=True)
        assert run.returncode == 0, run.stderr.decode(errors="replace")[-600:]

        subs, _attachments, _cover = _summary(output_path)
        assert [s["tags"]["title"] for s in subs] == SUBTITLES_ENG_FIRST
        assert [s["disposition"]["forced"] for s in subs] == [0, 0, 0, 1, 0]  # the dispositions follow their track


@pytest.mark.parametrize("engine", ENGINES)
def test_embed_poster_true_adds_cover_and_keeps_subtitles_and_fonts(world, monkeypatch, engine):
    monkeypatch.setattr(merge, "MUX_ENGINE", engine)
    _set_embed_poster(monkeypatch, True)
    out, _ = merge.join_media(world.video, [], world.subs, str(world.tmp / "out.mkv"))
    subs_before, attachments_before, _ = _summary(out)

    monkeypatch.setattr("VibraVid.core.downloader.base.embed_poster", merge.embed_poster)
    final = BaseDownloader._embed_poster(_downloader(), out)

    subs, attachments, cover = _summary(final)
    assert cover, "the cover must be attached"
    assert attachments == attachments_before, "the fonts must survive the poster pass with their own names"
    assert [s["codec_name"] for s in subs] == [s["codec_name"] for s in subs_before] == ["ass"] * 5
    assert subs[1]["disposition"]["forced"] == 1


@pytest.mark.parametrize("engine", ENGINES)
def test_embed_poster_false_leaves_the_muxed_file_byte_identical(world, monkeypatch, engine):
    monkeypatch.setattr(merge, "MUX_ENGINE", engine)
    _set_embed_poster(monkeypatch, False)
    out, _ = merge.join_media(world.video, [], world.subs, str(world.tmp / "out.mkv"))
    before = _digest(out)

    monkeypatch.setattr("VibraVid.core.downloader.base.embed_poster", merge.embed_poster)
    final = BaseDownloader._embed_poster(_downloader(), out)

    assert final == out and _digest(out) == before
    _subs, attachments, cover = _summary(out)
    assert not cover and "TestFont.ttf" in attachments


@pytest.mark.parametrize("engine", ENGINES)
def test_embed_fonts_off_still_keeps_ass_but_attaches_nothing(world, monkeypatch, engine):
    monkeypatch.setattr(merge, "MUX_ENGINE", engine)
    monkeypatch.setattr(font_embed, "embed_fonts_enabled", lambda: False)
    out, _ = merge.join_media(world.video, [], world.subs, str(world.tmp / "out.mkv"))

    subs, attachments, _cover = _summary(out)
    assert [s["codec_name"] for s in subs] == ["ass"] * 5
    assert attachments == []


@needs_ffmpeg
def test_mp4_output_flattens_subtitles_and_attaches_no_fonts(world, monkeypatch):
    monkeypatch.setattr(merge, "MUX_ENGINE", "ffmpeg")
    out, _ = merge.join_media(world.video, [], world.subs[:2], str(world.tmp / "out.mp4"))

    streams = _probe(out)
    assert [s["codec_name"] for s in streams if s["codec_type"] == "subtitle"] == ["mov_text", "mov_text"]
    assert not [s for s in streams if s["codec_type"] == "attachment"]


@needs_ffmpeg
def test_streaming_direct_fast_path_keeps_ass_and_fonts(world, monkeypatch):
    """The live-mux fast path of Generic_Downloader: video is piped to ffmpeg's stdin, sibling subtitles are plain inputs."""
    from VibraVid.core.downloader import _generic

    monkeypatch.setattr(_generic, "get_ffmpeg_path", lambda: FFMPEG)
    monkeypatch.setattr(_generic.config_manager, "config", _generic.config_manager.config)  # real config, untouched
    out = str(world.tmp / "live.mkv")
    fake = SimpleNamespace(output_path=out, custom_filters={"audio": "best", "subtitle": "best"})  # no select_subtitle of the user's config reorders the tracks
    entries = [
        {"label": f"s{i}", "source": {"role": "subtitle"}, "result": {"path": sub["path"], "language": sub["language"]}}
        for i, sub in enumerate(world.subs[:4])
    ]  # fmt: skip

    cmd, output_path = Generic_Downloader._build_streaming_mux_cmd_direct(fake, {"source": {"codecs": "avc1"}}, entries)

    assert "-attach" in cmd and "-c:s" not in cmd
    fragmented = world.tmp / "frag.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", world.video, "-c", "copy", "-movflags", "frag_keyframe+empty_moov", str(fragmented)], check=True)
    run = subprocess.run(cmd, input=fragmented.read_bytes(), capture_output=True)
    assert run.returncode == 0, run.stderr.decode(errors="replace")[-600:]

    subs, attachments, _cover = _summary(output_path)
    assert [s["codec_name"] for s in subs] == ["ass"] * 4
    assert {"TestFont.ttf", "ForcedFont.ttf", "CcFont.otf"} <= set(attachments)
    assert subs[1]["disposition"]["forced"] == 1 and subs[2]["disposition"]["hearing_impaired"] == 1
