"""Optional GUI login, bot-token path, and the opt-in gate for code-installing endpoints."""

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "GUI"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

import django

django.setup()

from django.core.management import call_command
from django.test import Client

from searchapp import security


@pytest.fixture(autouse=True)
def _db():
    call_command("migrate", verbosity=0, interactive=False)
    security.login_throttle._failures.clear()
    yield


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("VIBRAVID_USERNAME", "admin")
    monkeypatch.setenv("VIBRAVID_PASSWORD", "s3cret")


def _get(client, path, **extra):
    return client.get(path, HTTP_HOST="127.0.0.1", **extra)


def test_gui_is_open_without_credentials(monkeypatch):
    monkeypatch.delenv("VIBRAVID_USERNAME", raising=False)
    monkeypatch.delenv("VIBRAVID_PASSWORD", raising=False)
    assert _get(Client(), "/settings/").status_code == 200


def test_pages_redirect_to_login_and_api_gets_401(auth_on):
    client = Client()
    response = _get(client, "/settings/")
    assert response.status_code == 302 and response["Location"].startswith("/login/")
    api = _get(client, "/api/logs/list/")
    assert api.status_code == 401
    assert client.post("/api/save-settings/", data="{}", content_type="application/json", HTTP_HOST="127.0.0.1").status_code == 401
    assert client.post("/api/upload-service/", HTTP_HOST="127.0.0.1").status_code == 401


def test_login_flow_and_logout(auth_on):
    client = Client()
    bad = client.post("/login/", {"username": "admin", "password": "nope"}, HTTP_HOST="127.0.0.1")
    assert bad.status_code == 401
    ok = client.post("/login/", {"username": "admin", "password": "s3cret", "next": "/settings/"}, HTTP_HOST="127.0.0.1")
    assert ok.status_code == 302 and ok["Location"] == "/settings/"
    assert _get(client, "/settings/").status_code == 200
    client.post("/logout/", HTTP_HOST="127.0.0.1")
    assert _get(client, "/settings/").status_code == 302


def test_login_rejects_open_redirect(auth_on):
    response = Client().post("/login/", {"username": "admin", "password": "s3cret", "next": "https://evil.example/"}, HTTP_HOST="127.0.0.1")
    assert response.status_code == 302 and response["Location"] == "/"


def test_login_is_throttled_after_repeated_failures(auth_on):
    client = Client()
    for _ in range(security.LoginThrottle.MAX_FAILURES):
        client.post("/login/", {"username": "admin", "password": "x"}, HTTP_HOST="127.0.0.1")
    blocked = client.post("/login/", {"username": "admin", "password": "s3cret"}, HTTP_HOST="127.0.0.1")
    assert blocked.status_code == 429


def test_machine_endpoints_keep_their_own_auth(auth_on, monkeypatch):
    client = Client()
    monkeypatch.setenv("VIBRAVID_BOT_SECRET", "botsecret")
    assert _get(client, "/api/bot/sites/", HTTP_X_VIBRAVID_TOKEN="botsecret").status_code != 401
    assert _get(client, "/api/remote/health/").status_code == 200
    assert client.post("/api/arr/webhook/radarr/", data="{}", content_type="application/json", HTTP_HOST="127.0.0.1").status_code != 302

    # get-downloads: the Telegram sidecar authenticates by token, nobody else
    assert _get(client, "/api/get-downloads/").status_code == 401
    assert _get(client, "/api/get-downloads/", HTTP_X_VIBRAVID_TOKEN="wrong").status_code == 401
    assert _get(client, "/api/get-downloads/", HTTP_X_VIBRAVID_TOKEN="botsecret").status_code == 200


def test_service_upload_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIBRAVID_ALLOW_SERVICE_UPLOAD", raising=False)
    client = Client()
    assert client.post("/api/upload-service/", HTTP_HOST="127.0.0.1").status_code == 403
    assert client.post("/api/imp-service/refresh/", HTTP_HOST="127.0.0.1").status_code == 403


def test_hooks_and_imp_service_edits_are_blocked_unless_enabled(monkeypatch, tmp_path):
    from VibraVid.utils import config_manager

    monkeypatch.delenv("VIBRAVID_ALLOW_SERVICE_UPLOAD", raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text('[DEFAULT]\nimp_service = ["default"]\nauto_check_update = true\n', encoding="utf-8")
    monkeypatch.setattr(config_manager, "config_file_path", str(cfg))

    client = Client()

    def save(**body):
        return client.post("/api/save-settings/", data=json.dumps({"file_type": "config", **body}),
                           content_type="application/json", HTTP_HOST="127.0.0.1")

    blocked = save(patch={"HOOKS": {"post_download": [{"command": "touch /tmp/pwned"}]}})
    assert blocked.status_code == 403
    assert save(patch={"DEFAULT": {"imp_service": ["https://example.com/x.git"]}}).status_code == 403
    assert save(content='[DEFAULT]\nimp_service = ["/evil"]\n').status_code == 403
    assert "pwned" not in cfg.read_text(encoding="utf-8")


def test_invalid_toml_error_does_not_echo_the_parser_message(monkeypatch, tmp_path):
    """Code-scanning alert 67 (py/stack-trace-exposure): the parser text stays in the log."""
    from VibraVid.utils import config_manager

    cfg = tmp_path / "config.toml"
    cfg.write_text("[DEFAULT]\n", encoding="utf-8")
    monkeypatch.setattr(config_manager, "config_file_path", str(cfg))

    response = Client().post("/api/save-settings/", data=json.dumps({"file_type": "config", "content": "key = zzzmarker ="}),
                             content_type="application/json", HTTP_HOST="127.0.0.1")

    assert response.status_code == 400
    error = response.json()["error"]
    assert "zzzmarker" not in error and "line" not in error.lower() and "column" not in error.lower()
    assert cfg.read_text(encoding="utf-8") == "[DEFAULT]\n"  # nothing was written
