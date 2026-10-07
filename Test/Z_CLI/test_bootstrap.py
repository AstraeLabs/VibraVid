# 05.10.26
# ruff: noqa: E402

"""Every launcher goes through ``VibraVid.cli.bootstrap``: library-path fix first, traceback + pause + exit 1 on an unhandled error."""

import importlib.util
import sys
import types
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.cli import bootstrap


@pytest.fixture
def calls(monkeypatch):
    log = []
    monkeypatch.setattr(bootstrap, "fix_ld_library_path", lambda: log.append("fix"))
    monkeypatch.setattr(bootstrap, "pause_if_needed", lambda: log.append("pause"))
    return log


def test_the_library_path_is_fixed_before_the_entry_runs(calls):
    bootstrap.run_guarded(lambda: calls.append("entry"))

    assert calls == ["fix", "entry"]


def test_an_unhandled_error_prints_the_traceback_pauses_and_exits_with_1(calls, capsys):
    def boom():
        raise RuntimeError("kaput")

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.run_guarded(boom)

    assert excinfo.value.code == 1
    assert calls == ["fix", "pause"]
    assert "RuntimeError: kaput" in capsys.readouterr().err


@pytest.mark.parametrize("code", [0, 2, 130])
def test_system_exit_from_the_entry_passes_through_untouched(calls, code):
    def leave():
        raise SystemExit(code)

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.run_guarded(leave)

    assert excinfo.value.code == code
    assert "pause" not in calls


def test_keyboard_interrupt_ends_the_run_cleanly_with_exit_130(calls, capsys):
    def interrupted():
        raise KeyboardInterrupt

    with pytest.raises(SystemExit) as excinfo:
        bootstrap.run_guarded(interrupted)

    assert excinfo.value.code == 130
    assert "pause" not in calls
    out = capsys.readouterr()
    assert "Interrupted." in out.out and "Traceback" not in out.err


def _fake_module(monkeypatch, name, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


def test_main_runs_the_cli_through_the_guard(calls, monkeypatch):
    _fake_module(monkeypatch, "VibraVid.cli.run", main=lambda: calls.append("cli"))

    bootstrap.main()

    assert calls == ["fix", "cli"]


def _load_tui_launcher():
    spec = importlib.util.spec_from_file_location("tui_launcher", workspace_root / "tui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tui_launcher_with_arguments_runs_the_cli(calls, monkeypatch):
    _fake_module(monkeypatch, "VibraVid.cli.run", main=lambda: calls.append("cli"))
    _fake_module(monkeypatch, "VibraVid.tui.app", main=lambda: calls.append("tui"))
    monkeypatch.setattr(sys, "argv", ["tui.py", "--help"])

    _load_tui_launcher().run_tui_or_cli()

    assert calls == ["cli"]


def test_tui_launcher_without_arguments_opens_the_tui(calls, monkeypatch):
    _fake_module(monkeypatch, "VibraVid.cli.run", main=lambda: calls.append("cli"))
    _fake_module(monkeypatch, "VibraVid.tui.app", main=lambda: calls.append("tui"))
    monkeypatch.setattr(sys, "argv", ["tui.py"])

    _load_tui_launcher().run_tui_or_cli()

    assert calls == ["tui"]


def test_launchers_and_console_script_use_the_bootstrap():
    assert 'vibravid = "VibraVid.cli.bootstrap:main"' in (workspace_root / "pyproject.toml").read_text(encoding="utf-8")
    for launcher in ("manual.py", "tui.py", "VibraVid/__main__.py"):
        source = (workspace_root / launcher).read_text(encoding="utf-8")
        assert "bootstrap" in source and "traceback" not in source and "pause_if_needed" not in source


def test_the_cli_bootstrap_does_not_pull_the_tui_into_the_frozen_binary():
    """PyInstaller freezes manual.py: nothing reachable from it may import the TUI (textual)."""
    assert "VibraVid.tui" not in (workspace_root / "VibraVid" / "cli" / "bootstrap.py").read_text(encoding="utf-8")
