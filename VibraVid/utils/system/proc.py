# 26.08.26

import logging
import subprocess

logger = logging.getLogger(__name__)


def format_argv(cmd) -> str:
    """Render an argv list (or a ready string) as a single shell-ish line."""
    if isinstance(cmd, (str, bytes)):
        return cmd.decode() if isinstance(cmd, bytes) else cmd
    return " ".join(str(part) for part in cmd)


def log_command(cmd, description: str = "", *, level: int = logging.INFO, log: logging.Logger | None = None) -> None:
    """Emit a single, uniformly formatted line for an external command."""
    line = format_argv(cmd)
    prefix = f"{description}: " if description else "Running: "
    (log or logger).log(level, "%s%s", prefix, line)


def run_logged(cmd, description: str = "", *, level: int = logging.INFO, log: logging.Logger | None = None, **run_kwargs):
    """``subprocess.run(cmd, **run_kwargs)`` preceded by a uniform log line."""
    log_command(cmd, description, level=level, log=log)
    return subprocess.run(cmd, **run_kwargs)
