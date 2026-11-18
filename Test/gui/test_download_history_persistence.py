import json

from VibraVid.core.ui.tracker import DownloadTracker


class _HistoryRow:
    def __init__(self, download_id, payload, created_at):
        self.download_id = download_id
        self.payload = payload
        self.created_at = created_at


class _HistoryQuery(list):
    def __getitem__(self, key):
        return _HistoryQuery(super().__getitem__(key))


class _HistoryManager:
    def __init__(self):
        self.rows = []

    def create(self, download_id, payload):
        row = _HistoryRow(download_id, payload, len(self.rows) + 1)
        self.rows.append(row)
        return row

    def order_by(self, field):
        assert field == "-created_at"
        return _HistoryQuery(reversed(self.rows))


class _DownloadHistory:
    objects = _HistoryManager()


def _new_tracker():
    tracker = object.__new__(DownloadTracker)
    tracker._init_tracker()
    return tracker


def test_django_history_loading_is_deferred_until_apps_are_ready(monkeypatch):
    _DownloadHistory.objects.rows.clear()
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "webgui.settings")
    monkeypatch.setattr(DownloadTracker, "_django_apps_ready", lambda self: False)

    tracker = _new_tracker()

    assert tracker._history_loaded is False
    assert tracker.history == []

    entry = {
        "id": "persisted-download",
        "title": "Persistent history test",
        "site": "test",
        "type": "Film",
        "status": "completed",
        "progress": 100,
    }
    _DownloadHistory.objects.create(
        download_id=entry["id"],
        payload=json.dumps(entry),
    )

    monkeypatch.setattr(DownloadTracker, "_django_apps_ready", lambda self: True)
    monkeypatch.setattr(DownloadTracker, "_get_history_model", lambda self: _DownloadHistory)

    assert tracker.get_history() == [entry]
    assert tracker._history_loaded is True


def test_django_history_is_persisted_and_reloaded(monkeypatch):
    _DownloadHistory.objects.rows.clear()
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "webgui.settings")
    monkeypatch.setattr(DownloadTracker, "_django_apps_ready", lambda self: True)
    monkeypatch.setattr(DownloadTracker, "_get_history_model", lambda self: _DownloadHistory)

    tracker = _new_tracker()
    entry = {
        "id": "persisted-download",
        "title": "Persistent history test",
        "site": "test",
        "type": "Film",
        "status": "completed",
        "progress": 100,
    }

    tracker.history.append(entry)
    tracker._persist_history_entry(entry)

    assert len(_DownloadHistory.objects.rows) == 1
    assert json.loads(_DownloadHistory.objects.rows[0].payload) == entry

    reloaded = _new_tracker()

    assert reloaded.get_history() == [entry]
