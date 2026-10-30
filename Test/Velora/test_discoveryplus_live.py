"""Discovery+ live: record from the start of the event until the stream ends, not for the scheduled duration."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services.discoveryplus import downloader as dp


class _FakeLiveInfo:
    start = None
    end = None
    live = True

    def __init__(self, video_id):
        attrs = {}
        if self.start:
            attrs["scheduleStart"] = self.start.strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.end:
            attrs["scheduleEnd"] = self.end.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.content_info = {"attributes": attrs}

    def get_edit_id(self):
        return "edit-1"

    def is_live(self):
        return self.live

    def is_live_now(self):
        return self.live


class _FakeClient:
    def get_playback_info(self, edit_id):
        return {"manifest": "https://example.invalid/live.mpd", "license": "https://example.invalid/lic", "drm_type": "widevine"}


@pytest.fixture
def run(monkeypatch):
    captured = {}

    class FakeDash:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start(self):
            return "started"

    monkeypatch.setattr(dp, "DASH_Downloader", FakeDash)
    monkeypatch.setattr(dp, "GetLiveInfo", _FakeLiveInfo)
    monkeypatch.setattr(dp, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(dp, "start_message", lambda: None)
    monkeypatch.setattr(dp, "print_download_header", lambda *args, **kwargs: None)
    monkeypatch.setattr(context_tracker, "max_time", None)

    def go(start, end):
        _FakeLiveInfo.start, _FakeLiveInfo.end = start, end
        dp.download_live(SimpleNamespace(id="x", name="Some live event", year="2026"))
        return captured

    return go


def test_records_from_the_start_of_the_event_without_a_content_budget(run):
    now = datetime.now(UTC)
    args = run(now - timedelta(hours=2), now + timedelta(hours=1))

    assert args.get("max_time") is None
    assert args["live_tail_seconds"] == pytest.approx(2 * 3600 + 10, abs=30)


def test_an_event_that_has_not_started_takes_the_live_edge(run):
    now = datetime.now(UTC)
    args = run(now + timedelta(minutes=5), now + timedelta(hours=2))

    assert args.get("max_time") is None
    assert args["live_tail_seconds"] is None


def test_without_a_schedule_it_still_follows_the_live(run):
    args = run(None, None)

    assert args.get("max_time") is None
    assert args["live_tail_seconds"] is None


def test_explicit_max_time_wins_over_the_event_start(run, monkeypatch):
    monkeypatch.setattr(context_tracker, "max_time", "00:10:00")
    now = datetime.now(UTC)
    args = run(now - timedelta(hours=2), now + timedelta(hours=1))

    assert args["live_tail_seconds"] is None


def test_the_recording_stops_when_the_service_says_the_event_is_over(run):
    now = datetime.now(UTC)
    args = run(now - timedelta(hours=1), now + timedelta(hours=1))

    assert callable(args["stop_when"])
    assert args["stop_when"]() is False


def test_the_live_info_tells_live_from_finished():
    from VibraVid.services.discoveryplus.scrapper import GetLiveInfo

    info = GetLiveInfo.__new__(GetLiveInfo)
    info.video_id = "x"
    info.content_info = {"attributes": {"videoType": "LIVE"}}
    assert info.is_live()

    info.content_info = {"attributes": {"videoType": "STANDALONE_EVENT"}}
    assert not info.is_live()


def test_a_failed_recheck_is_not_taken_as_the_end():
    from VibraVid.services.discoveryplus.scrapper import GetLiveInfo

    info = GetLiveInfo.__new__(GetLiveInfo)
    info.video_id = "x"
    info.content_info = {"attributes": {"videoType": "LIVE"}}
    info._get_content_info = lambda: None

    with pytest.raises(RuntimeError):
        info.is_live_now()
