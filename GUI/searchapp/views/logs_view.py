# 23.08.26

import logging
from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)

_CATEGORY_DIRS = {"logs": ".cache/logs", "arr": ".cache/arr"}


def _log_dirs() -> dict[str, Path]:
    root = getattr(settings, "PROJECT_ROOT", None)
    if root is None:
        root = Path(getattr(settings, "BASE_DIR", "/app")).parent
    return {cat: Path(root) / rel for cat, rel in _CATEGORY_DIRS.items()}


def _list_dir(d: Path) -> list[dict]:
    if not d.is_dir():
        return []
    files = sorted(d.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
        for f in files
    ]


@require_http_methods(["GET"])
def logs_list(request: HttpRequest) -> JsonResponse:
    """List available log files, newest first, per category (app logs / ARR logs)."""
    dirs = _log_dirs()
    return JsonResponse({cat: _list_dir(d) for cat, d in dirs.items()})


@require_http_methods(["GET"])
def logs_content(request: HttpRequest) -> JsonResponse:
    """Return the tail of one log file. `name` must match an existing file in the category dir."""
    category = (request.GET.get("category") or "").strip()
    name = (request.GET.get("name") or "").strip()
    try:
        lines = int(request.GET.get("lines") or 500)
    except (TypeError, ValueError):
        lines = 500
    lines = max(1, min(lines, 2000))

    dirs = _log_dirs()
    d = dirs.get(category)
    if d is None:
        return JsonResponse({"error": "Invalid category"}, status=400)

    valid_names = {f.name for f in d.glob("*.log")} if d.is_dir() else set()
    if name not in valid_names:
        return JsonResponse({"error": "Unknown log file"}, status=400)

    try:
        with (d / name).open(encoding="utf-8", errors="replace") as fh:
            content_lines = fh.read().splitlines()
    except OSError as exc:
        logger.warning("Log file '%s/%s' non leggibile: %s", category, name, exc)
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({"name": name, "lines": content_lines[-lines:]})


__all__ = ["logs_list", "logs_content"]
