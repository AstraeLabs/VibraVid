"""track_downloads opt-out for the default vault (privacy advisory GHSA-j762-868g-pjcp)."""

import threading
from unittest.mock import patch

import pytest

from VibraVid.utils.vault import default as default_module
from VibraVid.utils.vault.default import Vault_di_default


@pytest.fixture
def vault():
    with patch.object(Vault_di_default, "warm"):
        yield Vault_di_default(name="t", base_url="https://vault.invalid")


def _with_cfg(cfg):
    return patch.object(default_module.config_manager.config, "get_dict", return_value={"default": cfg})


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), ("false", False), ("0", False), ("true", True)])
def test_flag_parsing(vault, value, expected):
    with _with_cfg({"track_downloads": value}):
        assert vault.is_tracking_enabled() is expected


def test_missing_key_defaults_to_enabled(vault):
    with _with_cfg({"url": "x"}):
        assert vault.is_tracking_enabled() is True


def _run_async(vault):
    done = threading.Event()
    calls = []
    with patch.object(vault, "track_download", side_effect=lambda **kw: calls.append(kw) or done.set()):
        vault.track_download_async("Title", "Film", "svc")
        done.wait(timeout=1)
        for t in threading.enumerate():
            if t is not threading.current_thread() and t.name.startswith("Thread-"):
                t.join(timeout=1)
    return calls


def test_tracking_off_sends_nothing(vault):
    with _with_cfg({"track_downloads": False}):
        assert _run_async(vault) == []


def test_tracking_on_sends_download(vault):
    with _with_cfg({"track_downloads": True}):
        assert len(_run_async(vault)) == 1
