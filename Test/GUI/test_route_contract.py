# 05.10.26
# ruff: noqa: E402

import io
import json
import os
import sys
import zipfile
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
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.urls import resolve, reverse

from searchapp import urls as app_urls


@pytest.fixture(scope="module")
def client():
    call_command("migrate", verbosity=0)
    return Client(HTTP_HOST="localhost")


def _post_json(client, url, body):
    return client.post(url, data=json.dumps(body) if not isinstance(body, str) else body, content_type="application/json")


# --- every route is wired to a callable view --------------------------------------------------------------------------
def test_every_route_resolves_to_its_view():
    for pattern in app_urls.urlpatterns:
        kwargs = {name: 1 for name in pattern.pattern.converters}
        url = reverse(pattern.name, kwargs=kwargs)
        assert callable(resolve(url).func), pattern.name


# --- pages and read-only endpoints -------------------------------------------------------------------------------------
@pytest.mark.parametrize("url", ["/", "/downloads/", "/watchlist/", "/settings/", "/logs/", "/arr-stack/"])
def test_pages_render(client, url):
    response = client.get(url)

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")


@pytest.mark.parametrize(
    "url, keys",
    [
        ("/api/get-downloads/", {"active", "scheduled", "history"}),
        ("/api/downloads-summary/", {"active", "queued", "history"}),
        ("/api/watchlist-status/", {"items", "scanning"}),
        ("/api/registry-status/", {"loaded_in_dropdown"}),
        ("/api/logs/list/", {"logs"}),
        ("/api/arr/status/", {"enabled", "polling_enabled", "webhook_enabled"}),
    ],
)
def test_read_only_json_endpoints(client, url, keys):
    response = client.get(url)

    assert response.status_code == 200
    assert keys <= set(response.json())


def test_site_cli_options_requires_a_site(client):
    response = client.get("/api/site-cli-options/")

    assert response.status_code == 400
    assert "site" in response.json()["error"]


# --- the wrong HTTP method is rejected before the view does anything ------------------------------------------------------
POST_ONLY = [
    "/download/",
    "/api/available-qualities/",
    "/series-metadata/",
    "/watchlist/add/",
    "/watchlist/remove/1/",
    "/watchlist/update/1/",
    "/watchlist/update-all/",
    "/watchlist/auto/1/",
    "/watchlist/auto-run/",
    "/watchlist/auto-interval/",
    "/watchlist/clear/",
    "/api/save-settings/",
    "/api/reload-config/",
    "/api/upload-service/",
    "/api/remove-service/",
    "/api/logs/clear/",
    "/api/arr/webhook/seerr/",
    "/api/arr/webhook/sonarr/",
    "/api/arr/webhook/radarr/",
    "/api/arr/trigger-sync/",
    "/api/arr/queue/1/delete/",
    "/api/version/update/",
    "/api/binaries/update/",
    "/api/kill-download/",
    "/api/remove-queued-download/",
    "/api/clear-queued-downloads/",
    "/api/kill-and-clear-queue/",
    "/api/stop-all-downloads/",
    "/api/clear-history/",
    "/api/resolve-tmdb-posters/",
]
GET_ONLY = [
    "/api/registry-status/",
    "/api/site-cli-options/",
    "/api/logs/list/",
    "/api/logs/content/",
    "/api/arr/status/",
    "/arr-stack/",
    "/api/version/check/",
]


@pytest.mark.parametrize("url", POST_ONLY)
def test_mutating_endpoints_refuse_get(client, url):
    assert client.get(url).status_code == 405


@pytest.mark.parametrize("url", GET_ONLY)
def test_read_only_endpoints_refuse_post(client, url):
    assert _post_json(client, url, {}).status_code == 405


# --- malformed input is a client error, never a server error -----------------------------------------------------------------
def test_save_settings_validation(client):
    assert _post_json(client, "/api/save-settings/", {}).status_code == 400
    assert _post_json(client, "/api/save-settings/", {"file_type": "config", "content": "{not json"}).status_code == 400
    assert _post_json(client, "/api/save-settings/", {"file_type": "nope", "content": "{}"}).status_code == 400


def test_save_settings_rejects_a_body_that_is_not_json(client):
    assert _post_json(client, "/api/save-settings/", "this is not json").status_code == 400
    assert _post_json(client, "/api/save-settings/", "[1, 2]").status_code == 400


def test_available_qualities_validation(client):
    for body in ({}, {"item_payload": {"name": "only a name"}}, {"item_payload": "{broken"}, "not json"):
        response = _post_json(client, "/api/available-qualities/", body)
        assert response.status_code == 400, body
        assert response.json()["qualities"] == []


def test_series_metadata_requires_parameters(client):
    response = _post_json(client, "/series-metadata/", {})

    assert response.status_code == 400
    assert "error" in response.json()


def test_upload_service_validation(client):
    assert client.post("/api/upload-service/").status_code == 400
    not_a_zip = SimpleUploadedFile("service.txt", b"hello")
    assert client.post("/api/upload-service/", {"service_zip": not_a_zip}).status_code == 400
    broken_zip = SimpleUploadedFile("service.zip", b"this is not a zip archive")
    assert client.post("/api/upload-service/", {"service_zip": broken_zip}).status_code == 400

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "no service in here")
    empty = SimpleUploadedFile("empty.zip", buffer.getvalue())
    response = client.post("/api/upload-service/", {"service_zip": empty})
    assert response.status_code == 400 and response.json()["success"] is False


def test_resolve_tmdb_posters_with_no_items_is_a_noop(client):
    response = _post_json(client, "/api/resolve-tmdb-posters/", {})

    assert response.status_code == 200
    assert response.json() == {"posters": {}, "episode_info": {}}


def test_invalid_form_posts_are_redirected_not_crashed(client):
    assert _post_json(client, "/download/", {}).status_code == 302
    assert _post_json(client, "/watchlist/add/", {}).status_code == 302
