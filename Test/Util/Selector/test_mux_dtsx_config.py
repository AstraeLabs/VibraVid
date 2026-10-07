# 05.10.26
# ruff: noqa: E402

import json
import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.core.utils import selector


class _FakeConfig:
    def __init__(self, data):
        self.data = data

    def get_bool(self, section, key, default=None):
        try:
            return bool(self.data[section][key])
        except KeyError:
            if default is None:
                raise ValueError(f"Key '{key}' not found in section '{section}'") from None
            return default


@pytest.fixture
def use_config(monkeypatch):
    def _apply(data):
        monkeypatch.setattr(selector, "config_manager", type("CM", (), {"config": _FakeConfig(data)})())

    return _apply


def test_defaults_to_false_when_the_key_is_nowhere(use_config):
    use_config({"PROCESS": {}, "CODEC": {"dv_auto": True}})
    assert selector.configured_mux_dtsx() is False


def test_reads_the_new_location(use_config):
    use_config({"PROCESS": {"mux_dtsx": True}, "CODEC": {"dv_auto": True}})
    assert selector.configured_mux_dtsx() is True


def test_old_codec_location_still_works(use_config):
    use_config({"PROCESS": {"force_subtitle": "auto"}, "CODEC": {"dv_auto": True, "mux_dtsx": True}})
    assert selector.configured_mux_dtsx() is True


def test_either_location_true_enables_it(use_config):
    use_config({"PROCESS": {"mux_dtsx": False}, "CODEC": {"mux_dtsx": True}})
    assert selector.configured_mux_dtsx() is True
    use_config({"PROCESS": {"mux_dtsx": True}, "CODEC": {"mux_dtsx": False}})
    assert selector.configured_mux_dtsx() is True


def test_both_false_is_off(use_config):
    use_config({"PROCESS": {"mux_dtsx": False}, "CODEC": {"mux_dtsx": False}})
    assert selector.configured_mux_dtsx() is False


def test_a_config_without_a_codec_section_does_not_raise(use_config):
    use_config({"PROCESS": {"mux_dtsx": True}})
    assert selector.configured_mux_dtsx() is True


def test_config_repair_does_not_turn_a_legacy_true_into_false(monkeypatch, use_config):
    """The real ConfigManager._repair_missing_config_keys merges the remote default into the user's file: with the new default it adds
    PROCESS.mux_dtsx = false. A user who had CODEC.mux_dtsx = true must keep DTS:X."""
    from VibraVid.utils import config as config_module

    manager = object.__new__(config_module.ConfigManager)
    manager._config_data = {"PROCESS": {"force_subtitle": "auto"}, "CODEC": {"dv_auto": True, "mux_dtsx": True}}
    manager.cache = {}
    manager.save_config = lambda: None

    class _Response:
        status_code = 200

        def json(self):
            return {"PROCESS": {"force_subtitle": "auto", "mux_dtsx": False}, "CODEC": {"dv_auto": True}}

    monkeypatch.setattr(config_module.requests, "get", lambda *a, **k: _Response())
    assert manager._repair_missing_config_keys() is True
    assert manager._config_data["PROCESS"]["mux_dtsx"] is False  # the repair really did add the new default next to the old key

    use_config(manager._config_data)
    assert selector.configured_mux_dtsx() is True


def test_shipped_config_has_it_under_process_right_after_force_subtitle():
    config = json.loads((workspace_root / "Conf" / "config.json").read_text(encoding="utf-8"))
    keys = list(config["PROCESS"])
    assert keys[keys.index("force_subtitle") + 1] == "mux_dtsx"
    assert "mux_dtsx" not in config["CODEC"]
