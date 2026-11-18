# 16.10.26

"""``YTDLPDownloader`` with a templated service name: media tokens must never reach yt-dlp's ``-o`` template."""

from pathlib import Path

import pytest

from VibraVid.core.downloader import yt_dlp_downloader
from VibraVid.core.downloader._media_tokens import resolve_media_tokens
from VibraVid.core.downloader.yt_dlp_downloader import YTDLPDownloader

TEMPLATE = "Movie Title (2020) [%(quality)]"


def test_resolve_media_tokens_fills_known_values_and_drops_empty_ones():
    assert resolve_media_tokens(TEMPLATE, {"quality": "1080p"}) == "Movie Title (2020) [1080p]"
    assert resolve_media_tokens(TEMPLATE, {"quality": ""}) == "Movie Title (2020)"
    assert resolve_media_tokens("A [%(quality)] [%(language)]", {"quality": "720p"}) == "A [720p]"


def test_the_download_runs_under_the_clean_name(tmp_path):
    downloader = YTDLPDownloader(url="https://x", output_dir=str(tmp_path), filename=TEMPLATE)

    assert downloader.filename == "Movie Title (2020)"
    assert "%(quality)" not in downloader.filename


def test_a_dotted_title_is_not_cut_at_the_dot(tmp_path):
    downloader = YTDLPDownloader(url="https://x", output_dir=str(tmp_path), filename="Mr. Robot [%(quality)]")

    assert downloader.filename == "Mr. Robot"


def test_the_finished_file_is_renamed_from_its_probed_metadata(tmp_path, monkeypatch):
    downloaded = tmp_path / "Movie Title (2020).mp4"
    downloaded.write_bytes(b"x")
    monkeypatch.setattr(yt_dlp_downloader, "get_media_metadata", lambda path: {"quality": "1080p"})
    downloader = YTDLPDownloader(url="https://x", output_dir=str(tmp_path), filename=TEMPLATE)

    renamed = downloader._resolve_media_tokens(downloaded)

    assert renamed == tmp_path / "Movie Title (2020) [1080p].mp4"
    assert renamed.is_file() and not downloaded.exists()


def test_a_failed_probe_keeps_the_file_untouched(tmp_path, monkeypatch):
    downloaded = tmp_path / "Movie Title (2020).mp4"
    downloaded.write_bytes(b"x")

    def boom(path):
        raise RuntimeError("ffprobe missing")

    monkeypatch.setattr(yt_dlp_downloader, "get_media_metadata", boom)
    downloader = YTDLPDownloader(url="https://x", output_dir=str(tmp_path), filename=TEMPLATE)

    assert downloader._resolve_media_tokens(downloaded) == downloaded
    assert downloaded.is_file()


@pytest.mark.parametrize("filename", ["100% Wolf", "Plain Title"])
def test_a_literal_percent_is_escaped_in_the_output_template(tmp_path, monkeypatch, filename):
    captured = {}

    class _Popen:
        stdout = None
        returncode = 0

        def __init__(self, cmd, **kwargs):
            captured["cmd"] = cmd

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(yt_dlp_downloader, "get_yt_dlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(yt_dlp_downloader.subprocess, "Popen", _Popen)
    YTDLPDownloader(url="https://x", output_dir=str(tmp_path), filename=filename).start()

    template = captured["cmd"][captured["cmd"].index("-o") + 1]
    assert template.endswith(".%(ext)s")
    assert Path(template[: -len(".%(ext)s")]).name == filename.replace("%", "%%")


class _Run:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_extract_info_returns_the_parsed_json_from_the_binary(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return _Run(stdout='{"title": "Mix", "entries": [{"id": "abc"}]}')

    monkeypatch.setattr(yt_dlp_downloader, "get_yt_dlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(yt_dlp_downloader.subprocess, "run", fake_run)

    info = YTDLPDownloader.extract_info("ytsearch3:query")

    assert info == {"title": "Mix", "entries": [{"id": "abc"}]}
    assert seen["cmd"][-1] == "ytsearch3:query" and "--flat-playlist" in seen["cmd"]


@pytest.mark.parametrize("proc", [_Run(returncode=1, stderr="boom"), _Run(stdout="not json")])
def test_extract_info_is_none_on_failure_or_bad_output(monkeypatch, proc):
    monkeypatch.setattr(yt_dlp_downloader, "get_yt_dlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(yt_dlp_downloader.subprocess, "run", lambda cmd, **kwargs: proc)

    assert YTDLPDownloader.extract_info("https://x") is None


def test_extract_info_is_none_without_the_binary(monkeypatch):
    monkeypatch.setattr(yt_dlp_downloader, "get_yt_dlp_path", lambda: None)

    assert YTDLPDownloader.extract_info("https://x") is None
