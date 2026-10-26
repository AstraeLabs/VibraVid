"""Configured language choices must affect actual audio tracks without leaking across jobs."""

import threading
from types import SimpleNamespace

import pytest

from VibraVid.core.downloader._generic import Generic_Downloader
from VibraVid.core.manifest.stream import Stream
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.audio_selection import (
    audio_filter_for_selection,
    audio_language_context,
    configured_audio_languages,
    filter_series_audio,
    normalize_audio_language,
)
from VibraVid.core.velora.base import BaseMediaDownloader
from VibraVid.utils import config_manager


@pytest.fixture(autouse=True)
def audio_config(monkeypatch):
    original = config_manager.config.get
    raw = {"value": "ita|eng|fra"}
    monkeypatch.setattr(config_manager.config, "get", lambda section, key, *args, **kwargs:
                        raw["value"] if (section, key) == ("DOWNLOAD", "select_audio") else original(section, key, *args, **kwargs))
    previous = dict(context_tracker.local.__dict__)
    context_tracker.audio_filter = None
    context_tracker.manual_tracks = False
    context_tracker.video_quality = None
    yield raw
    context_tracker.local.__dict__.clear()
    context_tracker.local.__dict__.update(previous)


@pytest.mark.parametrize(("raw", "expected"), [
    ("ita|eng|fra|jpn", ["it", "en", "fr", "ja"]),
    ("2eng|1ita|3fra", ["it", "en", "fr"]),
    ("l=fra|deu:c=aac", ["fr", "de"]),
    ("eng-US|eng-GB", ["en-US", "en-GB"]),
    ("ita|best", ["it"]),
    ("all", []),
])
def test_choices_come_from_config_grammar(raw, expected):
    assert [language["code"] for language in configured_audio_languages(raw)] == expected


def test_all_flattens_configured_languages_without_exclusive_priority(audio_config):
    audio_config["value"] = "1ita|2eng|3fra"
    assert audio_filter_for_selection("all") == "it|en|fr"
    assert audio_filter_for_selection("eng") == "en"
    with pytest.raises(ValueError):
        normalize_audio_language("jpn")


def test_all_config_can_choose_a_provider_reported_language(audio_config):
    audio_config["value"] = "all"
    assert normalize_audio_language("jpn") == "ja"
    with pytest.raises(ValueError):
        normalize_audio_language("xx")


def test_worker_choices_are_isolated_and_restored():
    ready = threading.Barrier(2)
    seen = {}
    def worker(choice):
        with audio_language_context(choice):
            ready.wait(timeout=3)
            seen[choice] = context_tracker.audio_filter
        assert context_tracker.audio_filter is None
    threads = [threading.Thread(target=worker, args=(choice,)) for choice in ("en", "fr")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=4)
        assert not thread.is_alive()
    assert seen == {"en": "en", "fr": "fr"}
    assert context_tracker.audio_filter is None


def _tracks(languages):
    return [Stream(type="video", id="v", height=720, width=1280, codecs="avc1", format="hls")] + [
        Stream(type="audio", id=f"a-{language}", language=language, codecs="mp4a", bitrate=128000, format="hls")
        for language in languages
    ]


@pytest.mark.parametrize("engine", ["manifest", "generic"])
@pytest.mark.parametrize(("choice", "expected"), [("en", {"en"}), ("all", {"it", "en", "fr"})])
def test_actual_selection_downloads_only_requested_audio(engine, choice, expected):
    tracks = _tracks(["it", "en", "fr", "de"])
    with audio_language_context(choice):
        if engine == "manifest":
            dummy = SimpleNamespace(custom_filters={"dv_auto": False}, streams=tracks)
            BaseMediaDownloader._apply_selection(dummy)
        else:
            downloader = Generic_Downloader.__new__(Generic_Downloader)
            downloader.custom_filters = {"dv_auto": False}
            downloader._select([(SimpleNamespace(streams=tracks), {})])
    assert {track.language for track in tracks if track.type == "audio" and track.selected} == expected


@pytest.mark.parametrize("engine", ["manifest", "generic"])
def test_unavailable_single_language_never_falls_back_to_another(engine):
    tracks = _tracks(["it"])
    with audio_language_context("en"), pytest.raises(ValueError, match="Requested audio is unavailable"):
        if engine == "manifest":
            BaseMediaDownloader._apply_selection(SimpleNamespace(custom_filters={"dv_auto": False}, streams=tracks))
        else:
            downloader = Generic_Downloader.__new__(Generic_Downloader)
            downloader.custom_filters = {"dv_auto": False}
            downloader._select([(SimpleNamespace(streams=tracks), {})])
    assert not any(track.type == "audio" and track.selected for track in tracks)


def test_batch_filter_preserves_episode_numbers_and_original_metadata():
    first = SimpleNamespace(number=22, episodes=[SimpleNamespace(number=19, language="it"), SimpleNamespace(number=20, language="en,it")])
    latest = SimpleNamespace(number=23, episodes=[SimpleNamespace(number=1, language="en"), SimpleNamespace(number=2, language="en")])
    italian = filter_series_audio([first, latest], "it")
    assert [(season.number, [ep.number for ep in season.episodes]) for season in italian] == [(22, [19, 20])]
    english = filter_series_audio([first, latest], "en")
    assert [(season.number, [ep.number for ep in season.episodes]) for season in english] == [(22, [20]), (23, [1, 2])]
    assert len(first.episodes) == 2


def test_separate_source_audio_is_validated_after_sources_are_combined():
    tracks = _tracks([])
    with audio_language_context("en"):
        context_tracker.defer_manual_tracks = True
        BaseMediaDownloader._apply_selection(SimpleNamespace(custom_filters={"dv_auto": False}, streams=tracks))
        context_tracker.defer_manual_tracks = False
        audio = _tracks(["en"])[1:]
        downloader = Generic_Downloader.__new__(Generic_Downloader)
        downloader.custom_filters = {"dv_auto": False}
        selected = downloader._select([(SimpleNamespace(streams=tracks), {}), (SimpleNamespace(streams=audio), {})])
        assert any(stream.type == "audio" and stream.language == "en" for stream in selected)
