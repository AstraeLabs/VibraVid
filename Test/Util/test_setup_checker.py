# 05.10.26
# ruff: noqa: E402

import sys
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import pytest

from VibraVid.setup import checker

TERMUX_FLUX = [
    "[red]No prebuilt flux binary available for this Termux device.[/red]",
    "[cyan]If required, please compile it and place it in system PATH.[/cyan]",
]
TERMUX_MKV = "[cyan]Please install it using: [yellow]pkg install mkvtoolnix[/cyan]"

# function name -> (tool group used by binary_paths and the installation level, executable name, Termux policy, Termux messages)
TOOLS = {
    "check_flux": ("flux", "flux", "prebuilt", TERMUX_FLUX),
    "check_velora": ("velora", "velora", "retry", []),
    "check_yt_dlp": ("yt-dlp", "yt-dlp", "prebuilt", ["[red]No prebuilt yt-dlp binary for this Termux device.[/red]"]),
    "check_deno": ("deno", "deno", "prebuilt", ["[red]No prebuilt deno binary for this Termux device.[/red]"]),
    "check_mkvmerge": ("mkvtoolnix", "mkvmerge", "manual", ["[red]MKVToolNix (mkvmerge) is required on Termux.[/red]", TERMUX_MKV]),
    "check_mkvpropedit": ("mkvtoolnix", "mkvpropedit", "manual", ["[red]MKVToolNix (mkvpropedit) is required on Termux.[/red]", TERMUX_MKV]),
    "check_dovi_tool": (
        "dovi_tool",
        "dovi_tool",
        "dovi",
        [
            "[yellow]dovi_tool not found in Termux environment.[/yellow]",
            "[cyan]Please compile manually using: [yellow]cargo install --git https://github.com/quietvoid/dovi_tool[/cyan]",
        ],
    ),
}


class FakeBinaryPaths:
    def __init__(self):
        self.system = "linux"
        self.is_termux = False
        self.local = {}  # (group, exec) -> path
        self.download_result = None
        self.download_calls = []
        self.local_lookups = []

    def get_binary_path(self, group, exec_name):
        self.local_lookups.append((group, exec_name))
        return self.local.get((group, exec_name))

    def download_binary(self, group, exec_name):
        self.download_calls.append((group, exec_name))
        return self.download_result

    def ensure_binary_directory(self):
        raise AssertionError("dovi_tool must not try to build from source in these scenarios")


class Console:
    def __init__(self):
        self.lines = []

    def print(self, message, style=None, **kwargs):
        self.lines.append((message, style))


@pytest.fixture
def env(monkeypatch):
    class Env:
        paths = FakeBinaryPaths()
        console = Console()
        on_path = {}
        allowed = {"flux", "velora", "yt-dlp", "deno", "dovi_tool", "mkvtoolnix", "ffmpeg"}

    e = Env()
    monkeypatch.setattr(checker, "binary_paths", e.paths)
    monkeypatch.setattr(checker, "console", e.console)
    monkeypatch.setattr(checker.shutil, "which", lambda name, *a, **k: e.on_path.get(name))
    monkeypatch.setattr(checker, "_should_download", lambda group: group in e.allowed)
    return e


def _call(name, **kwargs):
    return getattr(checker, name)(**kwargs)


@pytest.mark.parametrize("fn", TOOLS)
def test_found_in_system_path_is_returned_without_touching_anything(env, fn):
    _, exe, _, _ = TOOLS[fn]
    env.on_path[exe] = f"/usr/bin/{exe}"

    assert _call(fn) == f"/usr/bin/{exe}"
    assert env.paths.local_lookups == [] and env.paths.download_calls == [] and env.console.lines == []


@pytest.mark.parametrize("fn", TOOLS)
def test_found_in_local_binary_directory(env, tmp_path, fn):
    group, exe, _, _ = TOOLS[fn]
    local = tmp_path / exe
    local.write_text("x")
    env.paths.local[(group, exe)] = str(local)

    assert _call(fn) == str(local)
    assert env.paths.download_calls == []


@pytest.mark.parametrize("fn", TOOLS)
def test_a_stale_local_entry_that_is_not_a_file_is_ignored(env, tmp_path, fn):
    group, exe, _, _ = TOOLS[fn]
    env.paths.local[(group, exe)] = str(tmp_path / "missing")

    assert _call(fn, download=False) is None


@pytest.mark.parametrize("fn", TOOLS)
def test_download_false_never_downloads_or_prints(env, fn):
    env.paths.download_result = "/dl/x"

    assert _call(fn, download=False) is None
    assert env.paths.download_calls == [] and env.console.lines == []


@pytest.mark.parametrize("fn", TOOLS)
def test_group_outside_the_installation_level_is_not_downloaded(env, fn):
    env.allowed = set()
    env.paths.download_result = "/dl/x"

    assert _call(fn) is None
    assert env.paths.download_calls == [] and env.console.lines == []


@pytest.mark.parametrize("fn", TOOLS)
def test_download_success_returns_the_downloaded_path(env, fn):
    group, exe, _, _ = TOOLS[fn]
    env.paths.download_result = f"/dl/{exe}"

    assert _call(fn) == f"/dl/{exe}"
    assert env.paths.download_calls == [(group, exe)]
    assert env.console.lines == []


@pytest.mark.parametrize("fn", TOOLS)
def test_download_failure_reports_it(env, fn):
    group, exe, _, _ = TOOLS[fn]
    env.paths.download_result = None

    assert _call(fn) is None
    assert env.paths.download_calls == [(group, exe)]
    assert env.console.lines == [(f"Failed to download {exe}", "red")]


@pytest.mark.parametrize("fn", TOOLS)
def test_windows_uses_the_exe_suffix_everywhere(env, fn):
    group, exe, _, _ = TOOLS[fn]
    env.paths.system = "windows"
    env.paths.download_result = f"C:/dl/{exe}.exe"

    assert _call(fn) == f"C:/dl/{exe}.exe"
    assert env.paths.local_lookups == [(group, f"{exe}.exe")]
    assert env.paths.download_calls == [(group, f"{exe}.exe")]


# ---- Termux: the only place where the tools really differ -------------------------------------------------------------

@pytest.mark.parametrize("fn", [f for f, spec in TOOLS.items() if spec[2] in ("prebuilt", "retry")])
def test_termux_prebuilt_download_success(env, fn):
    group, exe, _, _ = TOOLS[fn]
    env.paths.is_termux = True
    env.paths.download_result = f"/dl/{exe}"

    assert _call(fn) == f"/dl/{exe}"
    assert env.paths.download_calls == [(group, exe)]
    assert env.console.lines == []


@pytest.mark.parametrize("fn", [f for f, spec in TOOLS.items() if spec[2] == "prebuilt"])
def test_termux_prebuilt_failure_prints_the_hint_and_stops(env, fn):
    group, exe, _, messages = TOOLS[fn]
    env.paths.is_termux = True
    env.paths.download_result = None

    assert _call(fn) is None
    assert env.paths.download_calls == [(group, exe)]  # one attempt only
    assert [m for m, _ in env.console.lines] == messages


@pytest.mark.parametrize("fn", [f for f, spec in TOOLS.items() if spec[2] == "prebuilt"])
def test_termux_prebuilt_outside_the_installation_level_prints_the_hint_without_downloading(env, fn):
    _, _, _, messages = TOOLS[fn]
    env.paths.is_termux = True
    env.allowed = set()

    assert _call(fn) is None
    assert env.paths.download_calls == []
    assert [m for m, _ in env.console.lines] == messages


def test_termux_velora_retries_the_download_once_more_before_giving_up(env):
    env.paths.is_termux = True
    env.paths.download_result = None

    assert checker.check_velora() is None
    assert env.paths.download_calls == [("velora", "velora"), ("velora", "velora")]
    assert env.console.lines == [("Failed to download velora", "red")]


def test_termux_velora_outside_the_installation_level_does_nothing(env):
    env.paths.is_termux = True
    env.allowed = set()

    assert checker.check_velora() is None
    assert env.paths.download_calls == [] and env.console.lines == []


@pytest.mark.parametrize("fn", ["check_mkvmerge", "check_mkvpropedit"])
@pytest.mark.parametrize("allowed", [True, False])
def test_termux_mkvtoolnix_is_never_downloaded(env, fn, allowed):
    _, _, _, messages = TOOLS[fn]
    env.paths.is_termux = True
    env.allowed = {"mkvtoolnix"} if allowed else set()
    env.paths.download_result = "/dl/x"

    assert _call(fn) is None
    assert env.paths.download_calls == []
    assert [m for m, _ in env.console.lines] == messages


def test_termux_dovi_tool_without_cargo_prints_manual_instructions(env):
    _, _, _, messages = TOOLS["check_dovi_tool"]
    env.paths.is_termux = True

    assert checker.check_dovi_tool() is None
    assert env.paths.download_calls == []
    assert [m for m, _ in env.console.lines] == messages


def test_mkvmerge_and_mkvpropedit_are_independent_lookups(env):
    env.on_path["mkvmerge"] = "/usr/bin/mkvmerge"

    assert checker.check_mkvmerge() == "/usr/bin/mkvmerge"
    env.paths.download_result = "/dl/mkvpropedit"
    assert checker.check_mkvpropedit() == "/dl/mkvpropedit"
    assert env.paths.download_calls == [("mkvtoolnix", "mkvpropedit")]


# ---- installation levels ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "level, group, expected",
    [
        ("", "flux", True),
        ("", "yt-dlp", False),
        ("", "mkvtoolnix", False),
        ("yt", "deno", True),
        ("yt", "dovi_tool", False),
        ("full", "mkvtoolnix", True),
        ("bogus", "velora", True),
        ("bogus", "deno", False),
    ],
)
def test_installation_levels(monkeypatch, level, group, expected):
    monkeypatch.setattr(checker.config_manager, "config", type("C", (), {"get": staticmethod(lambda section, key: level)})())
    assert checker._should_download(group) is expected
