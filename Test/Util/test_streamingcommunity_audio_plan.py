"""Audio planning for providers that keep a language inside each catalog's video."""

from unittest.mock import Mock, patch

import pytest

from VibraVid.core.manifest.stream import Stream
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.audio_selection import audio_language_context, enforce_audio_selection
from VibraVid.services.streamingcommunity.audio import download_audio_options
from VibraVid.utils import config_manager


@pytest.fixture(autouse=True)
def config(monkeypatch):
    original = config_manager.config.get
    monkeypatch.setattr(config_manager.config, "get", lambda section, key, *args, **kwargs:
                        "ita|eng" if (section, key) == ("DOWNLOAD", "select_audio") else original(section, key, *args, **kwargs))
    previous = dict(context_tracker.local.__dict__)
    context_tracker.audio_filter = None
    context_tracker.manual_tracks = False
    yield
    context_tracker.local.__dict__.clear()
    context_tracker.local.__dict__.update(previous)


def _embedded(language):
    return {"embedded": language, "languages": [language], "content": "#EXTM3U", "streams": [
        Stream(type="video", codecs="avc1,mp4a", height=720, bitrate=2000000, playlist_url=f"https://example.test/{language}/720.m3u8"),
        Stream(type="video", codecs="avc1,mp4a", height=360, bitrate=500000, playlist_url=f"https://example.test/{language}/360.m3u8"),
    ]}


def test_single_english_audio_uses_only_english_source():
    resolve = Mock()
    with audio_language_context("en"), patch("VibraVid.services.streamingcommunity.audio.inspect_manifest", return_value=_embedded("en")):
        result = download_audio_options("https://example.test/en/master.m3u8", {}, "en", ["it", "en"], resolve)
    resolve.assert_not_called()
    assert result["embedded_audio_language"] == "en"
    assert result["other_tracks"] == []
    assert result["required_audio_languages"] == ["en"]


def test_both_audio_languages_reuse_primary_and_add_other_catalog_audio():
    resolve = Mock(return_value=("https://example.test/en/master.m3u8", {"Referer": "https://example.test/en"}))
    with audio_language_context("all"), patch("VibraVid.services.streamingcommunity.audio.inspect_manifest", side_effect=[_embedded("it"), _embedded("en")]):
        result = download_audio_options("https://example.test/it/master.m3u8", {}, "it", ["it", "en"], resolve)
    resolve.assert_called_once_with("en")
    assert result["embedded_audio_language"] == "it"
    assert set(result["required_audio_languages"]) == {"it", "en"}
    assert [(track["language"], track["url"]) for track in result["other_tracks"]] == [("en", "https://example.test/en/360.m3u8")]


def test_all_requested_keeps_english_only_episode_downloadable():
    with audio_language_context("all"), patch("VibraVid.services.streamingcommunity.audio.inspect_manifest", return_value=_embedded("en")):
        result = download_audio_options("https://example.test/en/master.m3u8", {}, "en", ["en"], Mock())
    assert result["required_audio_languages"] == ["en"]
    assert result["other_tracks"] == []


def test_manifest_with_both_audio_tracks_does_not_download_another_catalog():
    primary = {"embedded": None, "languages": ["it", "en"], "content": "#EXTM3U", "streams": []}
    resolve = Mock()
    with audio_language_context("all"), patch("VibraVid.services.streamingcommunity.audio.inspect_manifest", return_value=primary):
        result = download_audio_options("https://example.test/master.m3u8", {}, "it", ["it", "en"], resolve)
    resolve.assert_not_called()
    assert result["other_tracks"] == []


def test_embedded_audio_is_allowed_only_for_matching_declared_language():
    tracks = [Stream(type="video", selected=True)]
    with audio_language_context("en"):
        enforce_audio_selection(tracks, "en")
        with pytest.raises(ValueError):
            enforce_audio_selection(tracks, "it")


def test_different_audio_selections_have_separate_output_and_temporary_files(tmp_path, monkeypatch):
    from VibraVid.core.downloader.hls import HLS_Downloader
    monkeypatch.setenv("VIBRAVID_TEMP_DIR", str(tmp_path / "scratch"))
    with audio_language_context("en"):
        english = HLS_Downloader(m3u8_url="https://example.test/master.m3u8", output_path=str(tmp_path / "episode.mkv"),
                                 required_audio_languages=["en"])
    with audio_language_context("it"):
        italian = HLS_Downloader(m3u8_url="https://example.test/master.m3u8", output_path=str(tmp_path / "episode.mkv"),
                                 required_audio_languages=["it"])
    assert "[ENG]" in english.output_path
    assert "[ITA]" in italian.output_path
    assert english.output_path != italian.output_path
    assert english.output_dir != italian.output_dir
