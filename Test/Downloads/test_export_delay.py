# 19.10.26

from types import SimpleNamespace

import pytest

from VibraVid.core.downloader import base, mp4
from VibraVid.core.ui.tracker import context_tracker


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(base, "DELAY_SS", 15)
    monkeypatch.setattr(mp4, "DELAY_SS", 15)
    monkeypatch.setattr(base.time, "sleep", calls.append)
    monkeypatch.setattr(mp4.time, "sleep", calls.append)
    monkeypatch.setattr(context_tracker, "capture_keys", None)
    return calls


def test_export_waits_after_each_hls_dash_ism_episode(sleeps, monkeypatch):
    monkeypatch.setattr(context_tracker, "export_file", "out.json")
    stub = SimpleNamespace(output_path="ep.mkv")
    for _ in range(3):
        base.BaseDownloader._skip_download_result(stub)
    assert sleeps == [15, 15, 15]


def test_export_waits_after_each_mp4_episode(sleeps, monkeypatch):
    import VibraVid.core.downloader.util._export as _export
    monkeypatch.setattr(_export, "write_export", lambda media, file: file)
    monkeypatch.setattr(context_tracker, "export_file", "out.json")
    monkeypatch.setattr(context_tracker, "capture", None)
    for i in range(3):
        assert mp4.MP4_Downloader(f"https://x/{i}.mp4", f"ep{i}.mp4")[1] is False
    assert sleeps == [15, 15, 15]


def test_remote_capture_never_waits(sleeps, monkeypatch):
    monkeypatch.setattr(context_tracker, "export_file", None)
    monkeypatch.setattr(context_tracker, "capture_keys", [])
    base.BaseDownloader._skip_download_result(SimpleNamespace(output_path="ep.mkv"))
    assert sleeps == []
