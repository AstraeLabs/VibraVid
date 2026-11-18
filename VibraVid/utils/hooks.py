# 28.02.26

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Iterator
from typing import Any

from rich.console import Console

from VibraVid.utils import config_manager, os_manager

console = Console()
logger = logging.getLogger(__name__)
_HOOK_CONTEXT_LOCK = threading.Lock()
_LAST_HOOK_CONTEXT: dict[str, dict[str, Any]] = {}


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _expand_user_path(path: str) -> str:
    if not path:
        return path
    return os.path.normpath(os.path.expandvars(os.path.expanduser(path)))


def _safe_format(value: str, template_context: dict[str, str]) -> str:
    if not isinstance(value, str):
        return value
    try:
        return value.format_map(_SafeFormatDict(template_context))
    except Exception:
        return value


def _should_run_on_current_os(hook: dict) -> bool:
    allowed_systems = hook.get("os")
    if not allowed_systems:
        return True
    try:
        normalized = [str(system_name).strip().lower() for system_name in allowed_systems]
    except Exception:
        return True
    return os_manager.system in normalized


def _normalize_context(stage: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    raw_context = dict(context or {})
    normalized_path = raw_context.get("download_path") or raw_context.get("path") or ""
    normalized_path = os.path.abspath(str(normalized_path)) if normalized_path else ""
    filename = os.path.basename(normalized_path) if normalized_path else ""
    directory = os.path.dirname(normalized_path) if normalized_path else ""
    success = raw_context.get("success")
    logger.debug(f"Normalizing context for stage '{stage}': raw_context={raw_context}, normalized_path='{normalized_path}', filename='{filename}', directory='{directory}', success='{success}'")

    if isinstance(success, bool):
        success_str = "1" if success else "0"
    elif success is None or success == "":
        success_str = ""
    else:
        success_str = str(success)

    template_context = {
        "stage": str(stage),
        "download_id": str(raw_context.get("download_id", "")),
        "download_title": str(raw_context.get("download_title") or raw_context.get("title") or ""),
        "download_site": str(raw_context.get("download_site") or raw_context.get("site") or ""),
        "download_media_type": str(raw_context.get("download_media_type") or raw_context.get("media_type") or ""),
        "download_status": str(raw_context.get("download_status") or raw_context.get("status") or ""),
        "download_error": str(raw_context.get("download_error") or raw_context.get("error") or ""),
        "download_path": normalized_path,
        "download_dir": directory,
        "download_filename": filename,
        "download_success": success_str,
    }

    env_context = {
        "SC_HOOK_STAGE": template_context["stage"],
        "SC_DOWNLOAD_ID": template_context["download_id"],
        "SC_DOWNLOAD_TITLE": template_context["download_title"],
        "SC_DOWNLOAD_SITE": template_context["download_site"],
        "SC_DOWNLOAD_MEDIA_TYPE": template_context["download_media_type"],
        "SC_DOWNLOAD_STATUS": template_context["download_status"],
        "SC_DOWNLOAD_ERROR": template_context["download_error"],
        "SC_DOWNLOAD_PATH": template_context["download_path"],
        "SC_DOWNLOAD_DIR": template_context["download_dir"],
        "SC_DOWNLOAD_FILENAME": template_context["download_filename"],
        "SC_DOWNLOAD_SUCCESS": template_context["download_success"],
    }

    normalized_context = raw_context.copy()
    normalized_context.update(template_context)
    return normalized_context, env_context


def _build_command_for_hook(hook: dict, stage: str, context: dict[str, Any] | None = None) -> tuple[list, dict]:
    hook_type = str(hook.get("type", "")).strip().lower()
    normalized_context, env_context = _normalize_context(stage, context)
    script_path = hook.get("path")
    inline_command = hook.get("command")
    allow_inline_template = bool(hook.get("allow_inline_template", False))
    args = hook.get("args", [])
    env = hook.get("env") or {}
    workdir = hook.get("cwd")
    logger.debug(f"Building command for hook: type='{hook_type}', script_path='{script_path}', inline_command='{inline_command}', args='{args}', env='{env}', workdir='{workdir}'")

    if isinstance(args, str):
        args = [arg for arg in args.split(" ") if arg]
    elif not isinstance(args, list):
        args = []

    template_context = {key: "" if value is None else str(value) for key, value in normalized_context.items()}

    if script_path:
        script_path = _safe_format(script_path, template_context)
        script_path = _expand_user_path(script_path)
        if not os.path.isabs(script_path):
            script_path = os.path.abspath(script_path)

    if inline_command:
        inline_command = str(inline_command)
        if allow_inline_template:
            inline_command = _safe_format(inline_command, template_context)

    if workdir:
        workdir = _safe_format(workdir, template_context)
        workdir = _expand_user_path(workdir)

    formatted_args = [_safe_format(str(arg), template_context) for arg in args]

    base_env = os.environ.copy()
    base_env.update(env_context)
    for key, value in env.items():
        base_env[str(key)] = _safe_format(str(value), template_context)

    if hook_type == "python":
        if not script_path:
            raise ValueError("Missing 'path' for python hook")
        command = [sys.executable, script_path] + formatted_args
        logger.debug(f"Constructed python hook command: {command} with env: {base_env} and cwd: {workdir}")
        return ([item for item in command if item], {"env": base_env, "cwd": workdir})

    if os_manager.system in ("linux", "darwin"):
        if hook_type in ("bash", "sh", "shell"):
            if inline_command:
                command = ["/bin/bash", "-lc", inline_command]
            else:
                if not script_path:
                    raise ValueError("Missing 'path' for bash/sh hook")
                command = ["/bin/bash", script_path] + formatted_args
            logger.debug(f"Constructed shell hook command: {command} with env: {base_env} and cwd: {workdir}")
            return (command, {"env": base_env, "cwd": workdir})

    if os_manager.system == "windows":
        if hook_type in ("bat", "cmd", "shell"):
            if inline_command:
                command = ["cmd", "/c", inline_command]
            else:
                if not script_path:
                    raise ValueError("Missing 'path' for bat/cmd hook")
                command = ["cmd", "/c", script_path] + formatted_args
            logger.debug(f"Constructed shell hook command: {command} with env: {base_env} and cwd: {workdir}")
            return (command, {"env": base_env, "cwd": workdir})

    raise ValueError(f"Unsupported hook type '{hook_type}' on OS '{os_manager.system}'")


def _iter_hooks(stage: str) -> Iterator[dict]:
    try:
        hooks_list = config_manager.config.get_list("HOOKS", stage)
        if not isinstance(hooks_list, list):
            return
        for hook in hooks_list:
            if isinstance(hook, dict):
                yield hook
    except Exception:
        return


def remember_hook_context(stage: str, context: dict[str, Any] | None) -> None:
    if not stage or not context:
        return
    with _HOOK_CONTEXT_LOCK:
        _LAST_HOOK_CONTEXT[str(stage).strip().lower()] = dict(context)


def get_last_hook_context(stage: str) -> dict[str, Any]:
    with _HOOK_CONTEXT_LOCK:
        return dict(_LAST_HOOK_CONTEXT.get(str(stage).strip().lower(), {}))


def execute_hooks(stage: str, context: dict[str, Any] | None = None) -> None:
    stage = str(stage).strip().lower()
    if not stage:
        return

    if context:
        remember_hook_context(stage, context)

    for hook in _iter_hooks(stage):
        console.print(f"\n[green]Executing hook for stage '{stage}'...")
        name = hook.get("name") or f"{stage}_hook"
        enabled = hook.get("enabled", True)
        continue_on_error = hook.get("continue_on_error", True)
        timeout = hook.get("timeout")
        logger.debug(f"Processing hook '{name}': enabled={enabled}, continue_on_error={continue_on_error}, timeout={timeout}")

        if not enabled:
            continue

        if not _should_run_on_current_os(hook):
            continue

        try:
            command, popen_kwargs = _build_command_for_hook(hook, stage, context=context)
            if timeout is not None:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=int(timeout),
                    **popen_kwargs,
                )
            else:
                result = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    **popen_kwargs,
                )

            stdout = (result.stdout or "").strip()
            stderr = (result.stderr or "").strip()
            if stdout:
                console.print(f"[cyan][hook:{name} stdout]\n{stdout}")
            if stderr:
                logger.error(f"Hook '{name}' stderr: {stderr}")
                console.print(f"[yellow][hook:{name} stderr]\n{stderr}")

            if result.returncode != 0:
                message = f"Hook '{name}' exited with code {result.returncode}"
                if continue_on_error:
                    logger.error(message + " (continuing)")
                    continue
                raise SystemExit(result.returncode)

        except Exception as exc:
            message = f"Hook '{name}' failed: {str(exc)}"
            if continue_on_error:
                logger.error(message + " (continuing)")
                continue
            raise
