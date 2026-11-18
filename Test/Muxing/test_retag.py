# 05.10.26

import json
import subprocess
from types import SimpleNamespace

import pytest

from VibraVid.core.muxing import retag
from VibraVid.core.muxing.retag import build_container_tags, retag_file
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path


@pytest.fixture
def ctx(monkeypatch):
    """Replace the context_tracker used by retag with a controllable fake."""

    def _set(**tracker):
        base = dict(title="", media_type="", season=0, episode=0, episode_name="", site_name="")
        base.update(tracker)
        monkeypatch.setattr(retag, "context_tracker", SimpleNamespace(**base))

    return _set


# --- build_container_tags ---------------------------------------------------
def test_movie_tags(ctx):
    ctx(title="Film", media_type="Film", site_name="streamingcommunity")
    assert build_container_tags() == {"title": "[VibraVid] Film", "comment": "[VibraVid] Film", "encoder": "VibraVid"}


def test_episode_tags(ctx):
    ctx(title="Show", media_type="TV", season=2, episode=5, episode_name="Pilot", site_name="site")
    assert build_container_tags() == {
        "title": "[VibraVid] Show",
        "comment": "[VibraVid] Pilot",
        "show": "Show",
        "season_number": "2",
        "episode_sort": "5",
        "episode_id": "Pilot",
        "encoder": "VibraVid",
    }


def test_episode_without_name_uses_series_name_as_comment(ctx):
    ctx(title="Show", season=1, episode=1)
    tags = build_container_tags()
    assert tags["title"] == "[VibraVid] Show"
    assert tags["comment"] == "[VibraVid] Show"
    assert "episode_id" not in tags


def test_no_title_still_tags_encoder_only(ctx):
    ctx()
    assert build_container_tags() == {"encoder": "VibraVid"}


@pytest.fixture
def tag_format(monkeypatch):
    """Control OUTPUT.tag_format as seen by retag."""

    def _set(value):
        real_get = retag.config_manager.config.get

        def fake_get(section, key, *args, **kwargs):
            if (section, key) == ("OUTPUT", "tag_format"):
                return value
            return real_get(section, key, *args, **kwargs)

        monkeypatch.setattr(retag.config_manager.config, "get", fake_get)

    return _set


def test_tag_format_default_is_vibravid_prefix(ctx):
    ctx(title="Film", media_type="Film")
    assert build_container_tags()["title"] == "[VibraVid] Film"


def test_tag_format_custom_prefix(ctx, tag_format):
    ctx(title="Film", media_type="Film")
    tag_format("[Mio]")
    tags = build_container_tags()
    assert tags["title"] == "[Mio] Film"
    assert tags["comment"] == "[Mio] Film"


@pytest.mark.parametrize("value", ["", "   ", None])
def test_tag_format_empty_disables_tags(ctx, tag_format, value):
    ctx(title="Film", media_type="Film")
    tag_format(value)
    assert build_container_tags() == {}


@pytest.mark.parametrize("name", ["a.mkv", "a.mp4"])
def test_tag_format_empty_leaves_file_untouched(tmp_path, have_ffmpeg, ctx, tag_format, name):
    ctx(title="Film", media_type="Film")
    tag_format("")
    path = _make(tmp_path, name)
    before = open(path, "rb").read()

    assert retag_file(path) is False
    assert open(path, "rb").read() == before


# --- retag_file on real files ----------------------------------------------
def _ffmpeg(*args):
    subprocess.run([get_ffmpeg_path(), "-y", "-loglevel", "error", *args], check=True)


def _probe_tags(path):
    out = subprocess.run(
        [get_ffprobe_path(), "-v", "error", "-show_format", "-of", "json", path],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    return {k.lower(): v for k, v in json.loads(out)["format"].get("tags", {}).items()}


def _packets_md5(path):
    """MD5 of the copied streams: identical before/after proves nothing was re-encoded/re-muxed differently."""
    out = subprocess.run(
        [get_ffmpeg_path(), "-v", "error", "-i", path, "-map", "0", "-c", "copy", "-f", "md5", "-"],
        capture_output=True, text=True, check=True,
    ).stdout
    return out.strip()


@pytest.fixture(scope="module")
def have_ffmpeg():
    """The getters return None (they do not raise) when a tool is missing and Test/conftest.py has switched downloads off."""
    try:
        available = bool(get_ffmpeg_path() and get_ffprobe_path())
    except Exception:
        available = False
    if not available:
        pytest.skip("ffmpeg/ffprobe not available")


def _make(tmp_path, name):
    path = str(tmp_path / name)
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        "-metadata", "title=old title", "-metadata", "comment=old comment", "-metadata", "encoder=old",
        path,
    )
    return path


TAGS = {"title": "[VibraVid] Film ò & <x>", "comment": "[VibraVid] Ep", "encoder": "VibraVid", "show": "My Show", "season_number": "2", "episode_sort": "7"}


def test_retag_mkv_in_place(tmp_path, have_ffmpeg):
    if not retag._find_mkvpropedit():
        pytest.skip("mkvpropedit not available")
    path = _make(tmp_path, "a.mkv")
    before = _packets_md5(path)
    size = (tmp_path / "a.mkv").stat().st_size

    assert retag_file(path, TAGS) is True

    tags = _probe_tags(path)
    assert tags["title"] == TAGS["title"]
    assert tags["comment"] == TAGS["comment"]
    assert tags["encoder"] == "VibraVid"
    assert tags["show"] == "My Show"
    assert tags["season_number"] == "2"
    assert _packets_md5(path) == before
    # in-place edit: no rewrite of the media payload (size stays within a small header delta)
    assert abs((tmp_path / "a.mkv").stat().st_size - size) < 4096


def test_retag_mkv_is_idempotent(tmp_path, have_ffmpeg):
    if not retag._find_mkvpropedit():
        pytest.skip("mkvpropedit not available")
    path = _make(tmp_path, "a.mkv")
    assert retag_file(path, TAGS)
    assert retag_file(path, TAGS)
    assert _probe_tags(path)["comment"] == TAGS["comment"]


def test_retag_mp4_in_place(tmp_path, have_ffmpeg):
    path = _make(tmp_path, "a.mp4")
    before = _packets_md5(path)

    assert retag_file(path, TAGS) is True

    tags = _probe_tags(path)
    assert tags["title"] == TAGS["title"]
    assert tags["comment"] == TAGS["comment"]
    assert tags["encoder"] == "VibraVid"
    assert tags["show"] == "My Show"
    assert _packets_md5(path) == before


def test_retag_ts_is_skipped(tmp_path, have_ffmpeg):
    path = _make(tmp_path, "a.ts")
    before = (tmp_path / "a.ts").read_bytes()
    assert retag_file(path, TAGS) is False
    assert (tmp_path / "a.ts").read_bytes() == before


def test_retag_missing_file_or_empty_tags(tmp_path):
    assert retag_file(str(tmp_path / "nope.mkv"), TAGS) is False
    (tmp_path / "x.mkv").write_bytes(b"")
    assert retag_file(str(tmp_path / "x.mkv"), {}) is False


def test_retag_never_raises_on_corrupt_file(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not an mp4")
    assert retag_file(str(bad), TAGS) is False
    bad_mkv = tmp_path / "bad.mkv"
    bad_mkv.write_bytes(b"not an mkv")
    assert retag_file(str(bad_mkv), TAGS) is False


def test_retag_mkv_without_mkvpropedit_is_noop(tmp_path, have_ffmpeg, monkeypatch):
    path = _make(tmp_path, "a.mkv")
    before = _packets_md5(path)
    monkeypatch.setattr(retag, "_find_mkvpropedit", lambda: None)
    assert retag_file(path, TAGS) is False
    assert _probe_tags(path)["title"] == "old title"
    assert _packets_md5(path) == before
