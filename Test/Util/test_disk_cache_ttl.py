# 12.10.26
# ruff: noqa: E402

import sys
import time
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest

from VibraVid.utils import config_manager, disk_cache


@pytest.fixture(autouse=True)
def cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path))
    return tmp_path


def test_save_with_ttl_stores_expiry_and_leaves_input_untouched():
    data = {"token": "abc"}
    before = time.time()
    disk_cache.save("svc", "t", data, ttl=100)

    assert data == {"token": "abc"}
    stored = disk_cache.load("svc", "t")
    assert stored["token"] == "abc"
    assert before + 100 <= stored["expiry"] <= time.time() + 100


def test_save_without_ttl_is_unchanged():
    disk_cache.save("svc", "t", {"token": "abc"})
    assert disk_cache.load("svc", "t") == {"token": "abc"}


def test_save_ttl_does_not_touch_non_dict_values():
    disk_cache.save("svc", "s", "just a string", ttl=100)
    assert disk_cache.load("svc", "s") == "just a string"


def test_load_fresh_returns_value_while_valid():
    disk_cache.save("svc", "t", {"token": "abc"}, ttl=100)
    assert disk_cache.load_fresh("svc", "t")["token"] == "abc"


def test_load_fresh_none_when_expired_missing_or_not_a_dict():
    disk_cache.save("svc", "old", {"token": "abc", "expiry": time.time() - 1})
    disk_cache.save("svc", "s", "text")
    assert disk_cache.load_fresh("svc", "old") is None
    assert disk_cache.load_fresh("svc", "missing") is None
    assert disk_cache.load_fresh("svc", "s") is None


def test_load_fresh_honours_buffer_and_custom_key():
    disk_cache.save("svc", "t", {"token": "abc", "expiry": time.time() + 30, "next": time.time() + 500})
    assert disk_cache.load_fresh("svc", "t") is not None
    assert disk_cache.load_fresh("svc", "t", buffer_seconds=60) is None
    assert disk_cache.load_fresh("svc", "t", expiry_key="next", buffer_seconds=60) is not None


def test_lock_serializes_threads_and_releases():
    import threading

    inside = []
    overlap = []

    def worker():
        with disk_cache.lock("svc", "t", poll=0.01):
            inside.append(1)
            if len(inside) > 1:
                overlap.append(True)
            time.sleep(0.05)
            inside.pop()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert not overlap
    assert not list(Path(disk_cache.cache_path("svc", "t")).parent.glob("*.lock"))


def test_lock_released_when_body_raises():
    with pytest.raises(RuntimeError):
        with disk_cache.lock("svc", "t"):
            raise RuntimeError("boom")
    with disk_cache.lock("svc", "t", timeout=1):
        pass


def test_lock_times_out_while_held_and_breaks_stale_lock():
    import os

    with disk_cache.lock("svc", "t"):
        with pytest.raises(TimeoutError):
            with disk_cache.lock("svc", "t", timeout=0.2, poll=0.01):
                pass

    lock_path = os.path.splitext(disk_cache.cache_path("svc", "t"))[0] + ".lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    Path(lock_path).write_text("")
    old = time.time() - 1000
    os.utime(lock_path, (old, old))
    with disk_cache.lock("svc", "t", timeout=1, stale_after=90):
        pass
