# 19.10.26

import threading
from types import SimpleNamespace

import pytest

from VibraVid.core.ui.manual_tracks import manual_track_broker
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.tui.app import VibraVidApp
from VibraVid.tui.screens.tracks_modal import TrackSelectModal, describe_track


def _streams():
    return [
        SimpleNamespace(type="video", resolution="1920x1080", width=1920, height=1080, codecs="avc1", bitrate=5_000_000, selected=True),
        SimpleNamespace(type="video", resolution="1280x720", width=1280, height=720, codecs="avc1", bitrate=2_000_000, selected=False),
        SimpleNamespace(type="audio", language="ita", codecs="mp4a", channels="2", bitrate=128_000, selected=True),
    ]


def test_describe_track_is_one_readable_line():
    line = describe_track({"type": "audio", "language": "ita", "codecs": "ec-3", "channels": "6", "bitrate": 256_000, "forced": True})
    assert line.split() == ["audio", "6ch", "ec-3", "ita", "256", "kbps", "FORCED"]


@pytest.mark.anyio
async def test_a_waiting_download_gets_the_picker_and_the_answer():
    app = VibraVidApp()
    streams = _streams()
    result = {}

    def worker():
        result["chosen"] = manual_track_broker.choose("dl1", streams, cancelled=lambda: False, timeout=10)

    async with app.run_test(size=(120, 40)) as pilot:
        thread = threading.Thread(target=worker)
        thread.start()
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, TrackSelectModal):
                break
        assert isinstance(app.screen, TrackSelectModal)

        # the preselection is what the filters chose; add the 720p one and confirm
        await pilot.pause(0.3)
        tracks = app.screen.query_one("#tracks-list")
        assert sorted(tracks.selected) == [0, 2]
        tracks.select(1)
        await pilot.click("#submit-btn")
        await pilot.pause(0.5)
        thread.join(5)

    assert result["chosen"] == {0, 1, 2}
    assert [s.selected for s in streams] == [True, True, True]
    manual_track_broker.forget("dl1")


@pytest.mark.anyio
async def test_cancelling_the_picker_stops_the_download():
    app = VibraVidApp()
    streams = _streams()
    errors = []

    def worker():
        try:
            manual_track_broker.choose(
                "dl2", streams, cancelled=lambda: download_tracker.is_stopped("dl2"), timeout=10
            )
        except Exception as exc:
            errors.append(exc)

    async with app.run_test(size=(120, 40)) as pilot:
        download_tracker.start_download("dl2", "t", "site")
        thread = threading.Thread(target=worker)
        thread.start()
        for _ in range(40):
            await pilot.pause(0.1)
            if isinstance(app.screen, TrackSelectModal):
                break
        await pilot.pause(0.3)
        await pilot.click("#cancel-btn")
        await pilot.pause(0.8)
        thread.join(5)

    assert errors and "cancelled" in str(errors[0])
    download_tracker.clear_stop_request("dl2")
