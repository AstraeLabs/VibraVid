from pathlib import Path

import pytest

import VibraVid.core.velora.curl_bridge as curl_bridge
import VibraVid.core.velora.downloader as velora_downloader
from VibraVid.core.velora.downloader import MediaDownloader


@pytest.mark.parametrize(
    ("override", "global_value", "expected_backend"),
    [
        (True, False, "curl"),
        (False, True, "native"),
        (None, True, "curl"),
        (None, False, "native"),
    ],
)
def test_segment_backend_override(
    monkeypatch,
    tmp_path,
    override,
    global_value,
    expected_backend,
):
    called = []

    def fake_native(plan, **kwargs):
        called.append("native")
        return [{"path": plan["tasks"][0]["path"]}]

    def fake_curl(plan, **kwargs):
        called.append("curl")
        return [{"path": plan["tasks"][0]["path"]}]

    monkeypatch.setattr(
        velora_downloader,
        "run_download_plan",
        fake_native,
    )
    monkeypatch.setattr(
        velora_downloader,
        "run_download_plan_curl_cffi",
        fake_curl,
    )
    monkeypatch.setattr(
        velora_downloader,
        "get_proxy_url",
        lambda: None,
    )

    original_get_bool = velora_downloader.config_manager.config.get_bool

    def fake_get_bool(section, key, *args, **kwargs):
        if section == "DOWNLOAD" and key == "use_curl_cffi_segments":
            return global_value
        return original_get_bool(section, key, *args, **kwargs)

    monkeypatch.setattr(
        velora_downloader.config_manager.config,
        "get_bool",
        fake_get_bool,
    )

    downloader = MediaDownloader.__new__(MediaDownloader)
    downloader.use_curl_cffi_segments = override
    downloader.curl_cffi_segment_browser = "chrome"
    downloader._stop_check = lambda: False

    paths = downloader._run_dl(
        [
            {
                "number": 1,
                "url": "https://cdn.example/segment.ts",
            }
        ],
        tmp_path,
        {},
        None,
    )

    assert called == [expected_backend]
    assert paths == [Path(tmp_path / "seg_00001.ts")]


def test_segment_browser_override_is_forwarded(monkeypatch, tmp_path):
    captured = {}

    def fake_curl(plan, **kwargs):
        captured.update(plan)
        return [{"path": plan["tasks"][0]["path"]}]

    monkeypatch.setattr(
        velora_downloader,
        "run_download_plan_curl_cffi",
        fake_curl,
    )
    monkeypatch.setattr(
        velora_downloader,
        "get_proxy_url",
        lambda: None,
    )

    downloader = MediaDownloader.__new__(MediaDownloader)
    downloader.use_curl_cffi_segments = True
    downloader.curl_cffi_segment_browser = None
    downloader._stop_check = lambda: False

    paths = downloader._run_dl(
        [
            {
                "number": 1,
                "url": "https://cdn.example/segment.ts",
            }
        ],
        tmp_path,
        {},
        None,
    )

    assert captured["curl_cffi_browser"] is None
    assert paths == [Path(tmp_path / "seg_00001.ts")]


def test_curl_segment_client_honors_browser_profile(monkeypatch):
    created = []

    class FakeClient:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    def fake_create_client(**kwargs):
        client = FakeClient()
        created.append((kwargs, client))
        return client

    monkeypatch.setattr(curl_bridge, "create_client", fake_create_client)
    curl_bridge._thread_local.client = None
    curl_bridge._thread_local.client_key = None

    chrome_client = curl_bridge._get_thread_client(
        timeout=20,
        verify=True,
        proxy_url=None,
        browser="chrome",
    )
    plain_client = curl_bridge._get_thread_client(
        timeout=20,
        verify=True,
        proxy_url=None,
        browser=None,
    )

    assert created[0][0]["browser"] == "chrome"
    assert created[1][0]["browser"] is None
    assert chrome_client.closed is True
    assert plain_client is created[1][1]
