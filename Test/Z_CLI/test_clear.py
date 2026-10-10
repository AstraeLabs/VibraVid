# 10.10.26
# ruff: noqa: E402

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import pytest

from VibraVid.cli import run
from VibraVid.utils import config_manager, disk_cache


@pytest.fixture
def cache_root(tmp_path, monkeypatch):
    monkeypatch.setattr(config_manager, "base_path", str(tmp_path))
    for service in ("crunchyroll", "tubitv"):
        disk_cache.save(service, "token", {"token": service, "expiry": 1})
        disk_cache.save(service, "other", {"x": 1})
    return tmp_path


def _service_files(root, service):
    folder = Path(root) / ".cache" / "services" / service
    return sorted(p.name for p in folder.glob("*.json")) if folder.exists() else []


def _args(site, clear=True):
    return SimpleNamespace(site=site, clear=clear, item=None)


def _site_maps():
    func = SimpleNamespace(module_name="crunchyroll", indice=5)
    return {"5": func}, {"crunchyroll": func}


def _select(args, monkeypatch):
    # Stop right after the --clear step: no search should be started in these tests.
    monkeypatch.setattr(run, "context_tracker", SimpleNamespace(cli_site=None))
    by_index, by_name = _site_maps()
    try:
        run.handle_direct_site_selection(args, by_index, by_name, None)
    except Exception:
        pass  # the stub function is not callable; only the cache side effect matters


def test_clear_removes_only_the_selected_service(cache_root, monkeypatch):
    _select(_args("crunchyroll"), monkeypatch)
    assert _service_files(cache_root, "crunchyroll") == []
    assert _service_files(cache_root, "tubitv") == ["other.json", "token.json"]


def test_clear_by_index_matches_by_name(cache_root, monkeypatch):
    _select(_args("5"), monkeypatch)
    assert _service_files(cache_root, "crunchyroll") == []
    assert _service_files(cache_root, "tubitv") == ["other.json", "token.json"]


def test_clear_unknown_service_removes_nothing(cache_root, monkeypatch):
    _select(_args("nope"), monkeypatch)
    assert _service_files(cache_root, "crunchyroll") == ["other.json", "token.json"]
    assert _service_files(cache_root, "tubitv") == ["other.json", "token.json"]


def test_without_clear_flag_nothing_is_removed(cache_root, monkeypatch):
    _select(_args("crunchyroll", clear=False), monkeypatch)
    assert _service_files(cache_root, "crunchyroll") == ["other.json", "token.json"]


def test_clear_requires_a_service():
    assert run.clear_args_error(_args(None)) is not None
    assert run.clear_args_error(_args("")) is not None
    assert run.clear_args_error(_args("crunchyroll")) is None
    assert run.clear_args_error(_args(None, clear=False)) is None


def test_parser_accepts_clear_and_defaults_to_false():
    parser, _ = run.setup_argument_parser({})
    assert parser.parse_args(["-i", "crunchyroll", "--clear"]).clear is True
    assert parser.parse_args(["-i", "crunchyroll"]).clear is False
