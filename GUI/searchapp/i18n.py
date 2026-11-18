# 01.11.26

import json
import logging
from pathlib import Path

from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)

CATALOG_DIR = Path(__file__).resolve().parent / "i18n"
COOKIE_NAME = "vv_lang"
LANGUAGE_NAMES = {"en": "English", "it": "Italiano"}

_cache: dict[str, tuple[float, dict]] = {}


def available_languages() -> list[str]:
    return ["en", *sorted(path.stem for path in CATALOG_DIR.glob("*.json"))]


def load_catalog(code: str) -> dict:
    """The catalog for ``code`` (re-read when the file changes); empty for English or an unknown code."""
    path = CATALOG_DIR / f"{code}.json"
    if code == "en" or not path.is_file():
        return {}
    mtime = path.stat().st_mtime
    cached = _cache.get(code)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("GUI catalog %s unreadable: %s", path.name, exc)
        catalog = {}
    _cache[code] = (mtime, catalog)
    return catalog


def _from_accept_language(header: str, languages: list[str]) -> str | None:
    for part in (header or "").split(","):
        code = part.split(";")[0].strip().lower().split("-")[0]
        if code in languages:
            return code
    return None


def resolve_language(request) -> str:
    languages = available_languages()
    chosen = request.COOKIES.get(COOKIE_NAME, "")
    if chosen in languages:
        return chosen
    try:
        configured = str(config_manager.config.get("DEFAULT", "gui_language", default="auto")).strip().lower()
    except Exception:
        configured = "auto"
    if configured in languages:
        return configured
    return _from_accept_language(request.META.get("HTTP_ACCEPT_LANGUAGE", ""), languages) or "en"
