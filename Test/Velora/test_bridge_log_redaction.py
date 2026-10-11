# 11.10.26

"""Regression coverage for sensitive URL logging in the Velora subprocess bridge."""

import io
import json
import logging

import pytest

from VibraVid.core.velora import bridge


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:password@cdn.example.test:8443/private/signed-path?token=secret-query#secret-fragment",
            "https://cdn.example.test",
        ),
        ("http://cdn.example.test/segment.ts?X-Amz-Signature=secret", "http://cdn.example.test"),
        ("https://[2001:db8::1]:443/private/key", "https://[2001:db8::1]"),
        ("https://[invalid", "[redacted URL]"),
        ("not-an-http-url?token=secret", "[redacted URL]"),
    ],
)
def test_redact_url_for_log_hides_credentials_and_signed_components(url, expected):
    assert bridge._redact_url_for_log(url) == expected


@pytest.mark.parametrize("event_type", ["completed", "retry", "error", "cancelled", "unknown"])
def test_formatted_velora_events_never_log_signed_urls(event_type):
    signed_url = "https://user:password@cdn.example.test/private/signed-path?token=secret-query#secret-fragment"
    event = {
        "event": event_type,
        "url": signed_url,
        "path": "/tmp/segment.bin",
        "message": f"Request failed for {signed_url}",
        "headers": {"Authorization": "Bearer secret-header-value"},
        "bytes": 123,
    }

    rendered = bridge._format_bridge_event(event)

    for sensitive in ("password", "signed-path", "secret-query", "secret-fragment", "secret-header-value"):
        assert sensitive not in rendered
    assert signed_url == event["url"]


def test_embedded_urls_are_redacted_from_retry_diagnostics():
    message = (
        "Request failed at https://first.example.test/signed/key?token=one "
        "then https://second.example.test/private/path?sig=two"
    )
    rendered = bridge._format_bridge_event({"event": "retry", "message": message, "attempt": 1, "retry_count": 3})

    assert "https://first.example.test" in rendered
    assert "https://second.example.test" in rendered
    assert "signed/key" not in rendered
    assert "token=one" not in rendered
    assert "sig=two" not in rendered


def test_subprocess_logs_are_redacted_without_mutating_callback_urls(monkeypatch, caplog):
    signed_url = "https://user:password@cdn.example.test/private/signed-path?token=secret-query#secret-fragment"
    path = "/tmp/velora-redaction-test-segment.ts"
    payload = "\n".join(
        [
            f"Non-JSON diagnostic: {signed_url}",
            json.dumps({"event": "retry", "path": path, "attempt": 1, "retry_count": 3,
                        "message": f"HTTP 416 while resuming {signed_url}"}),
            json.dumps({"event": "completed", "path": path, "bytes": 10}),
            json.dumps({"event": "unexpected", "path": path, "headers": {"Authorization": "Bearer secret-header-value"}}),
        ]
    ) + "\n"

    class FakePopen:
        def __init__(self, *args, **kwargs):
            self.stdout = io.StringIO(payload)
            self.stdin = io.StringIO()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(bridge, "get_velora_path", lambda: "fake-velora")
    monkeypatch.setattr(bridge.subprocess, "Popen", FakePopen)
    plan = {"tasks": [{"path": path, "url": signed_url}]}
    received = []

    with caplog.at_level(logging.DEBUG, logger="velora_bridge"):
        results = bridge.run_download_plan(plan, event_cb=received.append)

    assert results and results[0]["bytes"] == 10
    assert len(received) == 3
    assert all(event["url"] == signed_url for event in received)
    assert plan["tasks"][0]["url"] == signed_url
    assert "https://cdn.example.test" in caplog.text
    for sensitive in ("password", "signed-path", "secret-query", "secret-fragment", "secret-header-value"):
        assert sensitive not in caplog.text
