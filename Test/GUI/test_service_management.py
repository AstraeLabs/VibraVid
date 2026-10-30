# 06.10.26
# ruff: noqa: E402

import json
import os
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


def _compiled_service(root, name, suffix):
    variant = root / name / f"{name}_this_os_pyX"
    variant.mkdir(parents=True)
    (variant / f"{name}{suffix}").write_bytes(b"")


def test_a_compiled_service_counts_as_a_service_folder_and_is_removable(client, services):
    import importlib.machinery

    _compiled_service(services, "compiled_one", importlib.machinery.EXTENSION_SUFFIXES[0])

    assert services_admin.is_service_dir(str(services / "compiled_one"))
    assert not services_admin.is_service_dir(str(services / "no_init"))
    assert services_admin.removable_services() == ["compiled_one", "uploaded_one"]

    response = _post(client, {"name": "compiled_one"})
    assert response.status_code == 200
    assert not (services / "compiled_one").exists()


def _upload_zip(client, tmp_path, build_tree):
    import zipfile

    from django.core.files.uploadedfile import SimpleUploadedFile

    archive = tmp_path / "svc.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for arcname in build_tree:
            zf.writestr(arcname, b"")
    return client.post("/api/upload-service/", {"service_zip": SimpleUploadedFile("svc.zip", archive.read_bytes())})


def test_upload_installs_a_compiled_service_built_for_this_interpreter(client, services, tmp_path):
    import importlib.machinery

    suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    response = _upload_zip(client, tmp_path, [f"newsvc/newsvc_this_os_pyX/newsvc{suffix}"])

    assert response.status_code == 200
    assert response.json()["installed"] == ["newsvc"]
    assert (services / "newsvc" / "newsvc_this_os_pyX" / f"newsvc{suffix}").is_file()


def test_upload_refuses_a_compiled_service_without_a_matching_build(client, services, tmp_path):
    response = _upload_zip(client, tmp_path, ["newsvc/newsvc_other_os_py399/newsvc.cpython-399-nowhere-os.so"])

    assert response.status_code == 400
    assert any("nessuna build compatibile" in e for e in response.json()["errors"])
    assert not (services / "newsvc").exists()


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
