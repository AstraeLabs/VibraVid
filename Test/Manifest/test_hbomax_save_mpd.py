# 28.09.26

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import VibraVid.services.hbomax.downloader as hbomax_downloader
from VibraVid.core.ui.tracker import context_tracker


def _set_save_mpd(enabled: bool):
    context_tracker.site_options = {**(context_tracker.site_options or {}), "save_mpd": enabled}


def _clear_save_mpd():
    opts = dict(context_tracker.site_options or {})
    opts.pop("save_mpd", None)
    context_tracker.site_options = opts


def test_initial_fetch_never_saves_even_when_flag_is_on(monkeypatch):
    """raw.mpd is already written unconditionally by BaseMediaDownloader.parse_stream
    (velora/base.py) for every site -- the initial fetch here must not duplicate it."""
    saved = []
    monkeypatch.setattr(hbomax_downloader, "_fetch_manifest", lambda url, headers: "<MPD>raw</MPD>")
    monkeypatch.setattr(hbomax_downloader, "_save_mpd", lambda label, raw: saved.append(label))

    _set_save_mpd(True)
    try:
        manifest, _refresh = hbomax_downloader._manifest_with_refresh("https://example/manifest.mpd", {})
    finally:
        _clear_save_mpd()

    assert manifest == "<MPD>raw</MPD>"
    assert saved == []


def test_refresh_saves_only_when_flag_is_on(monkeypatch):
    monkeypatch.setattr(hbomax_downloader, "_fetch_manifest", lambda url, headers: "<MPD>fresh</MPD>")
    saved = []
    monkeypatch.setattr(hbomax_downloader, "_save_mpd", lambda label, raw: saved.append((label, raw)))

    _, refresh = hbomax_downloader._manifest_with_refresh("https://example/manifest.mpd", {})

    _set_save_mpd(True)
    try:
        result = refresh()
    finally:
        _clear_save_mpd()

    assert result == "<MPD>fresh</MPD>"
    assert saved == [("refresh", "<MPD>fresh</MPD>")]


def test_refresh_does_not_save_when_flag_is_off(monkeypatch):
    monkeypatch.setattr(hbomax_downloader, "_fetch_manifest", lambda url, headers: "<MPD>fresh</MPD>")
    saved = []
    monkeypatch.setattr(hbomax_downloader, "_save_mpd", lambda label, raw: saved.append((label, raw)))

    _, refresh = hbomax_downloader._manifest_with_refresh("https://example/manifest.mpd", {})

    _clear_save_mpd()
    result = refresh()

    assert result == "<MPD>fresh</MPD>"
    assert saved == []
