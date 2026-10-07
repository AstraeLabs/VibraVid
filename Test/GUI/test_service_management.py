# 06.10.26
# ruff: noqa: E402

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = ROOT / "GUI"
for path in (ROOT, GUI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

import pytest
from django.core.management import call_command
from django.test import Client

from searchapp import services_admin
from searchapp.views import settings_view

URL = "/api/remove-service/"


@pytest.fixture(scope="module")
def client():
    call_command("migrate", verbosity=0)
    return Client(HTTP_HOST="localhost")


@pytest.fixture
def services(tmp_path, monkeypatch):
    """A throw-away ``VibraVid/services`` with two built-ins, one uploaded service and helper folders."""
    root = tmp_path / "services"
    for name in ("streamingcommunity", "raiplay", "uploaded_one", "_base"):
        (root / name).mkdir(parents=True)
        (root / name / "__init__.py").write_text("indice = 1\n_useFor = 'x'\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / ".hidden").mkdir()
    (root / "no_init").mkdir()
    (root / "no_init" / "readme.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(services_admin, "services_dir", lambda: str(root))
    monkeypatch.setattr(settings_view, "_reload_service_registries", lambda names: [])
    return root


def _post(client, body):
    return client.post(URL, data=json.dumps(body) if not isinstance(body, str) else body, content_type="application/json")


# --- which folders count as removable ---------------------------------------------------------------------------------------
def test_removable_services_skip_builtins_helpers_and_folders_without_init(services):
    assert services_admin.removable_services() == ["uploaded_one"]


def test_builtin_list_matches_the_tracked_service_folders():
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "VibraVid/services"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=30
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("not a git checkout")
    if not tracked:
        pytest.skip("not a git checkout")

    folders = {p.split("/")[2] for p in tracked if p.count("/") >= 3 and not p.split("/")[2].startswith("_")}

    assert folders == set(services_admin.BUILTIN_SERVICES)


# --- the endpoint ------------------------------------------------------------------------------------------------------------
def test_removes_an_uploaded_service(client, services):
    response = _post(client, {"name": "uploaded_one"})

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True and data["removed"] == "uploaded_one" and data["errors"] == []
    assert not (services / "uploaded_one").exists()
    assert (services / "raiplay").is_dir() and (services / "streamingcommunity").is_dir()


@pytest.mark.parametrize("name", sorted(services_admin.BUILTIN_SERVICES))
def test_refuses_every_builtin(client, services, name):
    (services / name).mkdir(exist_ok=True)
    (services / name / "__init__.py").write_text("x", encoding="utf-8")

    response = _post(client, {"name": name})

    assert response.status_code == 403
    assert (services / name / "__init__.py").exists()


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", "..", "", "   ", "up load", "uploaded_one/..", None, 5, ["uploaded_one"]])
def test_rejects_invalid_names(client, services, name):
    response = _post(client, {"name": name})

    assert response.status_code == 400
    assert (services / "uploaded_one").exists()


@pytest.mark.parametrize("name", ["missing", "_base", "no_init", "__pycache__"])
def test_unknown_or_helper_folders_are_not_found(client, services, name):
    response = _post(client, {"name": name})

    assert response.status_code == 404
    assert (services / "_base").exists() and (services / "no_init").exists()


def test_does_not_follow_a_symlink(client, services, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    link = services / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    (outside / "__init__.py").write_text("x", encoding="utf-8")

    response = _post(client, {"name": "linked"})

    assert response.status_code == 403
    assert (outside / "keep.txt").exists()


@pytest.mark.parametrize("body", ["not json", "[]", "5", "null"])
def test_body_must_be_a_json_object(client, services, body):
    assert _post(client, body).status_code == 400


def test_get_is_not_allowed(client, services):
    assert client.get(URL).status_code == 405


def test_reload_errors_are_reported_but_the_folder_is_gone(client, services, monkeypatch):
    monkeypatch.setattr(settings_view, "_reload_service_registries", lambda names: ["Reload CLI services: boom"])

    data = _post(client, {"name": "uploaded_one"}).json()

    assert data["success"] is True and data["errors"] == ["Reload CLI services: boom"]
    assert not (services / "uploaded_one").exists()


def test_the_settings_page_lists_uploaded_services(client, services):
    page = client.get("/settings/").content.decode()

    assert 'data-remove="uploaded_one"' in page
    assert 'data-remove="streamingcommunity"' not in page  # built-ins never get a Remove button
