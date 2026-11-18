# 24.10.26

import importlib
import subprocess

import pytest

from VibraVid.setup.binary_paths import BinaryPaths, is_genuine_binary

bp = importlib.import_module("VibraVid.setup.binary_paths")  # `VibraVid.setup.binary_paths` as an attribute is the BinaryPaths instance

VELORA_VERSION = '{"name": "Velora", "version": "2.2.0"}'


@pytest.fixture
def answer(monkeypatch):
    """Make every `--version` call print what ``answer.out`` holds, counting the calls."""
    class Answer:
        out = ""
        calls = 0

    a = Answer()

    def run(cmd, **kwargs):
        a.calls += 1
        return subprocess.CompletedProcess(cmd, 0, stdout=a.out, stderr="")

    monkeypatch.setattr(bp.subprocess, "run", run)
    monkeypatch.setattr(bp, "_identity_cache", {})
    return a


def _binary(tmp_path, name="tool", data=b"x"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize(
    "tool, out, expected",
    [
        ("flux", "flux 0.9.3\n", True),
        ("flux", "flux version 2.3.0\n", False),  # Flux CD
        ("flux", "", False),
        ("velora", VELORA_VERSION, True),
        ("velora", "velora 2.2.0", False),
    ],
)
def test_the_version_output_decides(tmp_path, answer, tool, out, expected):
    answer.out = out

    assert is_genuine_binary(tool, _binary(tmp_path)) is expected


def test_a_tool_without_a_fingerprint_is_never_run(tmp_path, answer):
    assert is_genuine_binary("deno", _binary(tmp_path)) is True
    assert is_genuine_binary("flux", None) is True
    assert answer.calls == 0


def test_a_missing_file_is_not_genuine(tmp_path, answer):
    assert is_genuine_binary("flux", str(tmp_path / "missing")) is False
    assert answer.calls == 0


def test_a_binary_that_cannot_run_is_not_genuine(tmp_path, monkeypatch):
    monkeypatch.setattr(bp, "_identity_cache", {})

    def boom(*a, **k):
        raise OSError("exec format error")

    monkeypatch.setattr(bp.subprocess, "run", boom)

    assert is_genuine_binary("flux", _binary(tmp_path)) is False


def test_the_answer_is_cached_until_the_file_changes(tmp_path, answer):
    answer.out = "flux 0.9.3"
    path = _binary(tmp_path)

    assert is_genuine_binary("flux", path) and is_genuine_binary("flux", path)
    assert answer.calls == 1

    answer.out = "flux version 2.3.0"
    (tmp_path / "tool").write_bytes(b"a re-downloaded, different file")

    assert not is_genuine_binary("flux", path)
    assert answer.calls == 2


def test_a_look_alike_is_not_a_usable_binary(tmp_path, answer):
    answer.out = "flux version 2.3.0"
    look_alike = _binary(tmp_path, "flux")

    assert BinaryPaths._is_usable_file("flux", look_alike) is False
