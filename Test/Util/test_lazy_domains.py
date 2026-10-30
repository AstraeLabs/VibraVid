# 30.10.26

"""The startup domain fetch is awaited by the first domain read, not by the constructor."""

import json
import threading
import time

import pytest

from VibraVid.utils.bootstrap import _startup_prefetch
from VibraVid.utils.config import ConfigAccessor, ConfigManager

ONLINE = {"loonex": {"full_url": "https://online.example/"}}
LOCAL = {"loonex": {"full_url": "https://local.example/"}}


def _manager(tmp_path) -> ConfigManager:
    """A ConfigManager wired like the real constructor, but without reading config/login or starting the refresh thread."""
    manager = object.__new__(ConfigManager)
    manager.domains_path = str(tmp_path / "domains.json")
    manager.github_domains_path = str(tmp_path / "missing" / "domains.json")
    manager.cache = {}
    manager._domains_data = {}
    manager._domains_lock = threading.RLock()
    manager._domains_pending = False
    manager.fetch_domain_online = True
    manager.domain = ConfigAccessor(manager._domains_data, manager.cache, "domain", True, ready_fn=manager._ensure_domains)
    (tmp_path / "domains.json").write_text(json.dumps(LOCAL), encoding="utf-8")
    return manager


@pytest.fixture(autouse=True)
def prefetch(monkeypatch):
    """Replace the network job; ``state['collect']`` is what the 'domains' prefetch returns."""
    state = {"collect": ONLINE, "delay": 0.0, "calls": 0, "started": 0}

    def start():
        state["started"] += 1

    def collect(_key, timeout=None):
        state["calls"] += 1
        time.sleep(state["delay"] if timeout is None or timeout > 0 else 0)
        return state["collect"]

    monkeypatch.setattr(_startup_prefetch, "start", start)
    monkeypatch.setattr(_startup_prefetch, "collect", collect)
    return state


def test_loading_does_not_wait_for_the_fetch(tmp_path, prefetch):
    manager = _manager(tmp_path)
    prefetch["delay"] = 0.5

    began = time.perf_counter()
    manager._load_site_data()

    assert time.perf_counter() - began < 0.2
    assert prefetch["started"] == 1 and prefetch["calls"] == 0
    assert manager._domains_data == {}


def test_first_domain_read_gets_the_online_value(tmp_path):
    manager = _manager(tmp_path)
    manager._load_site_data()

    assert manager.domain.get("loonex", "full_url") == "https://online.example/"
    assert json.loads((tmp_path / "domains.json").read_text(encoding="utf-8")) == ONLINE


@pytest.mark.parametrize("read", [lambda d: d.get_section("loonex"), lambda d: d.has_section("loonex")])
def test_every_domain_read_waits_for_the_fetch(tmp_path, read):
    manager = _manager(tmp_path)
    manager._load_site_data()

    assert read(manager.domain)
    assert manager._domains_data == ONLINE


def test_failed_fetch_falls_back_to_the_local_file(tmp_path, prefetch):
    manager = _manager(tmp_path)
    prefetch["collect"] = None
    manager._load_site_data()

    assert manager.domain.get("loonex", "full_url") == "https://local.example/"


def test_concurrent_first_reads_resolve_once(tmp_path, prefetch):
    manager = _manager(tmp_path)
    prefetch["delay"] = 0.2
    manager._load_site_data()
    seen = []

    threads = [threading.Thread(target=lambda: seen.append(manager.domain.get("loonex", "full_url"))) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == ["https://online.example/"] * 6
    assert prefetch["calls"] == 1


def test_offline_setting_reads_the_file_without_any_fetch(tmp_path, prefetch):
    manager = _manager(tmp_path)
    manager.fetch_domain_online = False
    manager._load_site_data()

    assert manager.domain.get("loonex", "full_url") == "https://local.example/"
    assert prefetch["started"] == 0 and prefetch["calls"] == 0


def test_offline_setting_without_a_local_file_still_downloads_once(tmp_path, prefetch):
    manager = _manager(tmp_path)
    (tmp_path / "domains.json").unlink()
    manager.fetch_domain_online = False
    manager._load_site_data()

    assert manager.domain.get("loonex", "full_url") == "https://online.example/"
    assert prefetch["calls"] == 1
