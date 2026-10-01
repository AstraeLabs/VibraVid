import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for path in (ROOT, GUI_DIR):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

from GUI.searchapp import _download_infra as infra
from GUI.searchapp.views import dashboard


def _reset_queue_state():
    with infra.scheduled_downloads_lock:
        infra.scheduled_downloads.clear()
        infra.cancelled_scheduled_downloads.clear()


def setup_function():
    _reset_queue_state()


def teardown_function():
    _reset_queue_state()


def _add(download_id: str, title: str = "Test movie"):
    infra._add_scheduled_download(
        download_id,
        title,
        "mapple",
        media_type="Film",
    )


def _fake_json_response(data, status=200):
    return SimpleNamespace(data=data, status_code=status)


def test_remove_queued_download_removes_only_requested_item():
    _add("queued-1")
    _add("queued-2")

    assert infra._remove_queued_download("queued-1", active_ids=set()) is True

    assert "queued-1" not in infra.scheduled_downloads
    assert "queued-1" in infra.cancelled_scheduled_downloads
    assert "queued-2" in infra.scheduled_downloads


def test_remove_queued_download_refuses_active_item():
    _add("active-1")

    assert infra._remove_queued_download("active-1", active_ids={"active-1"}) is False

    assert "active-1" in infra.scheduled_downloads
    assert "active-1" not in infra.cancelled_scheduled_downloads


def test_clear_queued_downloads_keeps_active_items():
    _add("queued-1")
    _add("active-1")
    _add("queued-2")

    removed = infra._clear_queued_downloads(active_ids={"active-1"})

    assert removed == ["queued-1", "queued-2"]
    assert set(infra.scheduled_downloads) == {"active-1"}
    assert infra.cancelled_scheduled_downloads == {"queued-1", "queued-2"}


def test_remove_queued_download_view(monkeypatch):
    _add("queued-1")

    monkeypatch.setattr(dashboard, "JsonResponse", _fake_json_response)
    monkeypatch.setattr(
        dashboard,
        "download_tracker",
        SimpleNamespace(get_active_downloads=lambda: []),
    )

    request = SimpleNamespace(
        method="POST",
        body=json.dumps({"download_id": "queued-1"}).encode(),
    )
    response = dashboard.remove_queued_download(request)

    assert response.status_code == 200
    assert response.data == {"status": "success", "download_id": "queued-1"}
    assert "queued-1" not in infra.scheduled_downloads


def test_remove_queued_download_view_refuses_active(monkeypatch):
    _add("active-1")

    monkeypatch.setattr(dashboard, "JsonResponse", _fake_json_response)
    monkeypatch.setattr(
        dashboard,
        "download_tracker",
        SimpleNamespace(get_active_downloads=lambda: [{"id": "active-1"}]),
    )

    request = SimpleNamespace(
        method="POST",
        body=json.dumps({"download_id": "active-1"}).encode(),
    )
    response = dashboard.remove_queued_download(request)

    assert response.status_code == 409
    assert response.data["status"] == "error"
    assert "active-1" in infra.scheduled_downloads


def test_clear_queued_downloads_view_keeps_active(monkeypatch):
    _add("queued-1")
    _add("active-1")
    _add("queued-2")

    monkeypatch.setattr(dashboard, "JsonResponse", _fake_json_response)
    monkeypatch.setattr(
        dashboard,
        "download_tracker",
        SimpleNamespace(get_active_downloads=lambda: [{"id": "active-1"}]),
    )

    request = SimpleNamespace(method="POST", body=b"{}")
    response = dashboard.clear_queued_downloads(request)

    assert response.status_code == 200
    assert response.data["removed"] == 2
    assert response.data["download_ids"] == ["queued-1", "queued-2"]
    assert set(infra.scheduled_downloads) == {"active-1"}
