"""Check the actual output tracks using short locally generated media, without provider content."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from VibraVid.core.downloader import base
from VibraVid.core.downloader.hls import HLS_Downloader
from VibraVid.core.muxing.helper.video import get_media_metadata


@pytest.fixture(scope="module")
def local_media(tmp_path_factory):
    from VibraVid.setup import get_ffmpeg_path
    ffmpeg = shutil.which("ffmpeg") or get_ffmpeg_path()
    if not ffmpeg or not Path(ffmpeg).is_file():
        pytest.skip("ffmpeg is unavailable")
    directory = tmp_path_factory.mktemp("audio_mux")
    paths = {}
    for language, frequency in (("it", 440), ("en", 880)):
        path = directory / f"{language}.mkv"
        result = subprocess.run([str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=160x90:r=5", "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000",
            "-t", "2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-metadata:s:a:0",
            f"language={'ita' if language == 'it' else 'eng'}", str(path)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        paths[language] = path
    return paths


def _downloader(tmp_path, language, required, monkeypatch):
    downloader = HLS_Downloader.__new__(HLS_Downloader)
    downloader.output_path = str(tmp_path / "output.mkv")
    downloader.output_dir = str(tmp_path)
    downloader.filename_base = "output"
    downloader.download_id = None
    downloader.media_downloader = None
    downloader.embedded_audio_language = language
    downloader.required_audio_languages = required
    downloader.chapters = None
    downloader._error = None
    downloader._decrypt_failure_message = Mock(return_value=None)
    downloader._embed_poster = lambda path: path
    downloader._merge_output_ok = lambda path: bool(path and Path(path).is_file())
    monkeypatch.setattr(base, "build_hybrid_output", lambda **kwargs: None)
    monkeypatch.setattr(base, "MERGE_AUDIO", True)
    return downloader


def test_single_english_selection_produces_one_english_audio(local_media, tmp_path, monkeypatch):
    downloader = _downloader(tmp_path, "en", ["en"], monkeypatch)
    output = downloader._merge_files({"video": {"path": str(local_media["en"])}, "audios": []})
    assert output, downloader.error
    tracks = get_media_metadata(output)["audio_tracks"]
    assert [track["language"] for track in tracks] == ["ENG"]


def test_both_separate_catalog_audios_are_present_in_one_file(local_media, tmp_path, monkeypatch):
    downloader = _downloader(tmp_path, "it", ["it", "en"], monkeypatch)
    output = downloader._merge_files({"video": {"path": str(local_media["it"])}, "audios": [],
        "other_tracks_downloaded": [{"path": str(local_media["en"]), "kind": "audio", "language": "en"}]})
    assert output, downloader.error
    tracks = get_media_metadata(output)["audio_tracks"]
    assert sorted(track["language"] for track in tracks) == ["ENG", "ITA"]


def test_missing_second_audio_does_not_save_a_partial_selection(local_media, tmp_path, monkeypatch):
    downloader = _downloader(tmp_path, "it", ["it", "en"], monkeypatch)
    assert downloader._merge_files({"video": {"path": str(local_media["it"])}, "audios": []}) is None
    assert "requested audio track failed" in downloader.error
    assert not Path(downloader.output_path).exists()


def test_mux_dropping_an_audio_track_is_rejected(local_media, tmp_path, monkeypatch):
    downloader = _downloader(tmp_path, "it", ["it", "en"], monkeypatch)
    monkeypatch.setattr(base.BaseDownloader, "_merge_files", lambda self, status: str(local_media["en"]))
    assert downloader._merge_files({}) is None
    assert "every requested available audio" in downloader.error
