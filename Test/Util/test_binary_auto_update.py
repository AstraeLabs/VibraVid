"""Offline contract of the 2-day automatic binary check."""

import importlib
import json
import time

import pytest

updater = importlib.import_module("VibraVid.utils.upload.update")


@pytest.fixture
def env(monkeypatch, tmp_path):
    state = {"enabled": True, "results": {"ffmpeg": {"success": True, "updated": False, "local": "1", "latest": "1"}}, "runs": 0}

    def _run(**kwargs):
        state["runs"] += 1
        return state["results"]

    monkeypatch.setattr(updater.config_manager, "base_path", str(tmp_path))
    monkeypatch.setattr(updater.config_manager.config, "get_bool", lambda *a, **k: state["enabled"])
    monkeypatch.setattr(updater, "check_all_binaries_update", _run)
    state["stamp"] = tmp_path / ".cache" / "vibravid_binary_check.json"
    return state


def test_runs_without_stamp_then_waits_two_days(env):
    updater.maybe_auto_update_binaries()
    assert env["runs"] == 1
    assert env["stamp"].exists()

    updater.maybe_auto_update_binaries()
    assert env["runs"] == 1


def test_runs_again_after_two_days(env):
    env["stamp"].parent.mkdir(parents=True)
    env["stamp"].write_text(json.dumps({"checked_at": time.time() - 2 * 24 * 60 * 60 - 60}), encoding="utf-8")
    updater.maybe_auto_update_binaries()
    assert env["runs"] == 1


def test_offline_check_does_not_start_the_wait(env):
    env["results"] = {"ffmpeg": {"success": False, "message": "Could not fetch the latest ffmpeg version."}}
    updater.maybe_auto_update_binaries()
    assert env["runs"] == 1
    assert not env["stamp"].exists()


def test_failed_update_is_retried_at_next_start(env):
    env["results"] = {"ffmpeg": {"success": True, "updated": False, "local": "1", "latest": "2"}}
    updater.maybe_auto_update_binaries()
    assert not env["stamp"].exists()


def test_completed_update_starts_the_wait(env):
    env["results"] = {"ffmpeg": {"success": True, "updated": True, "local": "1", "latest": "2"}}
    updater.maybe_auto_update_binaries()
    assert env["stamp"].exists()


def test_disabled_by_config(env):
    env["enabled"] = False
    assert updater.maybe_auto_update_binaries() is None
    assert env["runs"] == 0
