# 05.10.26
# ruff: noqa: E402

"""``/api/bot/*`` (the bridge used by the Telegram bot) and the ARR webhooks: shared-secret handling and input validation."""

import json
import logging
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

from searchapp import api_bot
from searchapp.arr import arr_service

SECRET = "s3cret-token"
HEADER = api_bot.BOT_TOKEN_HEADER


@pytest.fixture(scope="module")
def client():
    call_command("migrate", verbosity=0)
    return Client(HTTP_HOST="localhost")


def _post(client, url, body, **headers):
    return client.post(url, data=json.dumps(body), content_type="application/json", **headers)


# --- /api/bot/*: shared secret --------------------------------------------------------------------------------------------
BOT_GET = ["/api/bot/sites/", "/api/bot/status/", "/api/bot/logs/"]
BOT_POST = ["/api/bot/search/", "/api/bot/seasons/", "/api/bot/download/", "/api/bot/cancel/"]


@pytest.mark.parametrize("url", BOT_GET)
def test_bot_get_endpoints_are_open_when_no_secret_is_configured(client, monkeypatch, url):
    monkeypatch.delenv("VIBRAVID_BOT_SECRET", raising=False)

    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", BOT_GET)
def test_bot_get_endpoints_need_the_secret_when_one_is_configured(client, monkeypatch, url):
    monkeypatch.setenv("VIBRAVID_BOT_SECRET", SECRET)

    assert client.get(url).status_code == 403
    assert client.get(url, headers={HEADER: "wrong"}).status_code == 403
    assert client.get(url, headers={HEADER: SECRET}).status_code == 200


@pytest.mark.parametrize("url", BOT_POST)
def test_bot_post_endpoints_need_the_secret_when_one_is_configured(client, monkeypatch, url):
    monkeypatch.setenv("VIBRAVID_BOT_SECRET", SECRET)

    assert _post(client, url, {}).status_code == 403
    assert _post(client, url, {}, headers={HEADER: "wrong"}).status_code == 403
    assert _post(client, url, {}, headers={HEADER: SECRET}).status_code == 400  # authorized, but the body is empty


@pytest.mark.parametrize(
    "url, message",
    [
        ("/api/bot/search/", "query mancante"),
        ("/api/bot/seasons/", "parametri mancanti"),
        ("/api/bot/download/", "parametri mancanti"),
        ("/api/bot/cancel/", "download_id o series_name mancante"),
    ],
)
def test_bot_post_endpoints_validate_their_input(client, monkeypatch, url, message):
    monkeypatch.delenv("VIBRAVID_BOT_SECRET", raising=False)
    response = _post(client, url, {})

    assert response.status_code == 400
    assert response.json() == {"error": message}


@pytest.mark.parametrize("url", BOT_POST)
def test_bot_post_endpoints_treat_a_body_that_is_not_json_as_empty(client, monkeypatch, url):
    monkeypatch.delenv("VIBRAVID_BOT_SECRET", raising=False)
    response = client.post(url, data="not json", content_type="application/json")

    assert response.status_code == 400


@pytest.mark.parametrize("url", BOT_GET)
def test_bot_get_endpoints_refuse_post(client, monkeypatch, url):
    monkeypatch.delenv("VIBRAVID_BOT_SECRET", raising=False)

    assert _post(client, url, {}).status_code == 405


# --- ARR webhooks ------------------------------------------------------------------------------------------------------------

WEBHOOKS = [
    ("/api/arr/webhook/seerr/", "seerr", "enable_seerr_webhook"),
    ("/api/arr/webhook/sonarr/", "sonarr_webhook", "enable_sonarr_webhook"),
    ("/api/arr/webhook/radarr/", "radarr_webhook", "enable_radarr_webhook"),
]


def _arr_config(monkeypatch, **overrides):
    config = {"enabled": True, "enable_seerr_webhook": True, "enable_sonarr_webhook": True, "enable_radarr_webhook": True}
    config.update(overrides)
    monkeypatch.setattr(arr_service, "_load_arr_config", lambda: config)


@pytest.mark.parametrize("url, section, flag", WEBHOOKS)
def test_webhook_is_ignored_when_arr_is_disabled(client, monkeypatch, url, section, flag):
    _arr_config(monkeypatch, enabled=False)
    response = _post(client, url, {})

    assert response.status_code == 200 and response.json()["status"] == "disabled"


@pytest.mark.parametrize("url, section, flag", WEBHOOKS)
def test_webhook_is_ignored_when_its_own_switch_is_off(client, monkeypatch, url, section, flag):
    _arr_config(monkeypatch, **{flag: False})
    response = _post(client, url, {})

    assert response.status_code == 200 and response.json()["status"] == "disabled"


@pytest.mark.parametrize("url, section, flag", WEBHOOKS)
def test_webhook_rejects_a_missing_or_wrong_token(client, monkeypatch, url, section, flag):
    _arr_config(monkeypatch, **{section: {"webhook_secret": SECRET}})

    assert _post(client, url, {}).status_code == 403
    assert _post(client, url, {}, headers={"X-Webhook-Token": "wrong"}).status_code == 403


@pytest.mark.parametrize("url, section, flag", WEBHOOKS)
def test_webhook_with_the_right_token_still_validates_the_body(client, monkeypatch, url, section, flag):
    _arr_config(monkeypatch, **{section: {"webhook_secret": SECRET}})
    response = client.post(url, data="not json", content_type="application/json", headers={"X-Webhook-Token": SECRET})

    assert response.status_code == 400
    assert response.json()["message"] == "Invalid JSON"


@pytest.mark.parametrize("url, section, flag", WEBHOOKS)
@pytest.mark.parametrize("valid_token", [False, True])
def test_webhook_does_not_log_auth_headers_or_raw_body(client, monkeypatch, caplog, url, section, flag, valid_token):
    secret = "webhook-secret-should-not-be-logged"
    supplied_token = secret if valid_token else "incorrect-token-should-not-be-logged"
    _arr_config(monkeypatch, **{section: {"webhook_secret": secret}})

    with caplog.at_level(logging.INFO, logger="searchapp.views.arr"):
        response = client.post(
            url,
            data="invalid-json-with-sensitive-body-value",
            content_type="application/json",
            headers={
                "X-Webhook-Token": supplied_token,
                "Authorization": "Bearer sensitive-authorization-value",
            },
        )

    assert response.status_code == (400 if valid_token else 403)
    for sensitive in (
        secret,
        supplied_token,
        "sensitive-authorization-value",
        "sensitive-body-value",
    ):
        assert sensitive not in caplog.text


@pytest.mark.parametrize("url, section, flag", WEBHOOKS)
def test_webhook_does_not_log_parsed_test_payload(client, monkeypatch, caplog, url, section, flag):
    secret = "webhook-secret-should-not-be-logged"
    _arr_config(monkeypatch, **{section: {"webhook_secret": secret}})
    payload = {
        "notification_type": "TEST_NOTIFICATION",
        "eventType": "Test",
        "media": {"media_type": "movie"},
        "series": {"id": 1},
        "movie": {"id": 1},
        "private": "sensitive-json-payload-value",
    }

    with caplog.at_level(logging.INFO, logger="searchapp.views.arr"):
        response = _post(client, url, payload, headers={"X-Webhook-Token": secret})

    assert response.status_code == 200
    assert secret not in caplog.text
    assert "sensitive-json-payload-value" not in caplog.text
