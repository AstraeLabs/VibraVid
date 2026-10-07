# 06.10.26

"""``get_site_name_from_stack``: cheap on the context-variable path, one log line per service, no ``inspect.stack()`` on the fallback path."""

import inspect
import logging
import os
import sys
import threading

import pytest

from VibraVid.services._base import site_costant
from VibraVid.services._base.site_loader import current_site_var, folder_name


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(site_costant, "_last_logged_site", None, raising=False)
    token = current_site_var.set(None)
    yield
    current_site_var.reset(token)


def _debug_lines(caplog):
    return [r.getMessage() for r in caplog.records if "Extracted site_name" in r.getMessage()]


def test_context_variable_wins_and_is_logged_once_per_service(caplog):
    caplog.set_level(logging.DEBUG, logger=site_costant.logger.name)
    current_site_var.set("streamingcommunity")

    names = {site_costant.get_site_name_from_stack() for _ in range(100)}

    assert names == {"streamingcommunity"}
    assert len(_debug_lines(caplog)) == 1


def test_a_change_of_service_is_logged_again(caplog):
    caplog.set_level(logging.DEBUG, logger=site_costant.logger.name)

    for name in ("raiplay", "raiplay", "la7", "la7", "raiplay"):
        current_site_var.set(name)
        assert site_costant.get_site_name_from_stack() == name

    assert [line.rsplit(": ", 1)[1] for line in _debug_lines(caplog)] == ["raiplay", "la7", "raiplay"]


def _make_service(tmp_path, name="fakeservice"):
    """A real module on disk inside ``<tmp>/services/<name>/`` that calls get_site_name_from_stack, imported from that path."""
    service_dir = tmp_path / folder_name / name
    service_dir.mkdir(parents=True)
    (service_dir / "__init__.py").write_text("", encoding="utf-8")
    (service_dir / "probe.py").write_text(
        "from VibraVid.services._base.site_costant import get_site_name_from_stack\n\ndef who():\n    return get_site_name_from_stack()\n",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path / folder_name))
    return service_dir


def test_fallback_finds_the_service_from_the_calling_module(tmp_path, monkeypatch):
    service_dir = _make_service(tmp_path)
    monkeypatch.setattr(inspect, "stack", lambda *a, **k: pytest.fail("inspect.stack() must not be used"))
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("probe_fakeservice", service_dir / "probe.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert module.who() == "fakeservice"
    finally:
        sys.path.remove(str(tmp_path / folder_name))


def test_fallback_returns_none_outside_any_service(caplog):
    """In a bare worker thread (no context variable) whose only frames are an anonymous <probe> code object and threading.py."""
    caplog.set_level(logging.ERROR, logger=site_costant.logger.name)
    namespace = {"get": site_costant.get_site_name_from_stack, "box": []}
    exec(compile("def run():\n    box.append(get())\n", "<probe>", "exec"), namespace)
    worker = threading.Thread(target=namespace["run"])
    worker.start()
    worker.join()

    assert namespace["box"] == [None]
    assert "Could not extract site_name" in caplog.text


def test_directory_checks_are_cached(tmp_path):
    folder = tmp_path / "svc"
    folder.mkdir()
    (folder / "__init__.py").write_text("", encoding="utf-8")
    site_costant._init_dirs.clear()

    calls = []
    real_exists = os.path.exists
    try:
        os.path.exists = lambda p: (calls.append(p), real_exists(p))[1]
        assert site_costant._has_init(str(folder)) is True
        assert site_costant._has_init(str(folder)) is True
    finally:
        os.path.exists = real_exists

    assert len(calls) == 1
