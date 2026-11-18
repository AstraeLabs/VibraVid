import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "docker" / "entrypoint.sh"
SH = shutil.which("bash") or shutil.which("sh")

pytestmark = pytest.mark.skipif(SH is None, reason="no POSIX shell available")

STUBS = {
    "id": 'if [ "$1" = "-u" ]; then echo 1000; else echo 1000; fi',
    "ls": "echo seeded",
    "mkdir": "exit 0",
    "usermod": "exit 0",
    "groupmod": "exit 0",
    "gosu": "exit 0",
    "chown": 'echo "$@" >> "$CALLS_FILE"',
}


def _run_entrypoint(tmp_path, env_extra):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body + "\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    calls = tmp_path / "chown_calls.txt"
    calls.write_text("")
    env = {k: v for k, v in os.environ.items() if k not in ("PUID", "PGID", "SKIP_MEDIA_CHOWN")}
    env.update({"PATH": f"{bin_dir.as_posix()}{os.pathsep}{env['PATH']}", "CALLS_FILE": calls.as_posix()})
    env.update(env_extra)

    result = subprocess.run([SH, ENTRYPOINT.as_posix()], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    recursive = [line for line in calls.read_text().splitlines() if line.startswith("-R")]
    return recursive, result.stdout


def test_default_chowns_media_library(tmp_path):
    recursive, _ = _run_entrypoint(tmp_path, {"PUID": "1000", "PGID": "1000"})
    joined = "\n".join(recursive)
    assert "/app/Video" in joined
    assert "/app/Conf" in joined


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_skip_media_chown_keeps_internal_dirs(tmp_path, value):
    recursive, stdout = _run_entrypoint(tmp_path, {"PUID": "1000", "SKIP_MEDIA_CHOWN": value})
    joined = "\n".join(recursive)
    assert "/app/Video" not in joined
    assert "/app/Conf" in joined and "/app/data" in joined
    assert "skipping recursive chown of /app/Video" in stdout


def test_falsy_value_keeps_default(tmp_path):
    recursive, _ = _run_entrypoint(tmp_path, {"PUID": "1000", "SKIP_MEDIA_CHOWN": "0"})
    assert "/app/Video" in "\n".join(recursive)


def test_no_puid_pgid_no_recursive_chown(tmp_path):
    recursive, _ = _run_entrypoint(tmp_path, {})
    assert recursive == []
