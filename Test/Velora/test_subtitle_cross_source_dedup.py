# 26.11.26

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from VibraVid.core.manifest.stream import Stream
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.velora import base as base_module


def _external_selected(monkeypatch, tmp_path, manifest_subs: list[Stream], external: dict) -> bool:
    """Run parse_stream with *manifest_subs* already selected and one external subtitle the filter accepts; return its _selected."""
    streams = [Stream(type="video", selected=True), *manifest_subs]

    class FakeHLSParser:
        def __init__(self, *args, **kwargs):
            pass

        def fetch_manifest(self):
            return True

        def save_raw(self, directory):
            return tmp_path / "test.m3u8"

        def parse_streams(self):
            return streams

    monkeypatch.setattr(base_module, "HLSParser", FakeHLSParser)
    monkeypatch.setattr(base_module.manual_track_broker, "choose", lambda _job, offered, **_k: {i for i, s in enumerate(offered) if s.selected})
    downloader = base_module.BaseMediaDownloader.__new__(base_module.BaseMediaDownloader)
    downloader.download_id = "dedup-test"
    downloader.url = "https://example.invalid/test.m3u8"
    downloader.manifest_protocol = "hls"
    downloader.manifest_content = None
    downloader.headers = {}
    downloader.has_drm = False
    downloader._tmp_dir = tmp_path
    downloader.external_subtitles = [external]
    downloader.external_audios = []
    downloader.external_other_tracks = []
    downloader._apply_selection = lambda: None
    downloader._register_dv_companion = lambda: None
    downloader._ext_track_matches = lambda *_: True
    downloader._ext_lang_matches = lambda *_: True

    old = (context_tracker.is_gui, context_tracker.manual_tracks, context_tracker.defer_manual_tracks, context_tracker.download_id)
    try:
        context_tracker.is_gui = True
        context_tracker.manual_tracks = True
        context_tracker.defer_manual_tracks = False
        context_tracker.download_id = "dedup-test"
        downloader.parse_stream(show_table=False)
    finally:
        context_tracker.is_gui, context_tracker.manual_tracks, context_tracker.defer_manual_tracks, context_tracker.download_id = old
    return external["_selected"]


def _sub(language: str, **flags) -> Stream:
    return Stream(type="subtitle", language=language, selected=True, **flags)


@pytest.mark.parametrize(
    "manifest, external, kept",
    [
        ([_sub("it-IT")], {"language": "it-it", "format": "ttml2"}, False),                       # same plain variant: manifest wins
        ([_sub("en-US", is_cc=True)], {"language": "en-us", "sdh": True}, False),                  # CC and SDH are one hearing variant
        ([_sub("it")], {"language": "it-IT"}, False),                                              # no region on one side
        ([_sub("it-IT")], {"language": "it-it", "forced": True}, True),                            # forced stays distinct
        ([_sub("pt-BR")], {"language": "pt-pt"}, True),                                            # different region
        ([_sub("it-IT", forced=True)], {"language": "it-it"}, True),                               # manifest only has the forced one
        ([], {"language": "it-it"}, True),                                                         # nothing from the manifest
    ],
)
def test_external_subtitle_dropped_only_when_the_manifest_has_the_same_variant(monkeypatch, tmp_path, manifest, external, kept):
    assert _external_selected(monkeypatch, tmp_path, manifest, dict(external)) is kept


def test_unselected_manifest_subtitle_does_not_hide_the_external(monkeypatch, tmp_path):
    manifest = [Stream(type="subtitle", language="it-IT", selected=False)]
    assert _external_selected(monkeypatch, tmp_path, manifest, {"language": "it-it"}) is True
