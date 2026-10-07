# 06.06.25

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    raw_value = os.environ.get(name, default)
    normalized = raw_value.replace(",", " ")
    return [item.strip() for item in normalized.split() if item.strip()]


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Database location: prefer DJANGO_DB_DIR (set in Docker), fall back to the
# legacy in-app path so local dev keeps working.
_DB_DIR = Path(os.environ.get("DJANGO_DB_DIR") or BASE_DIR)
try:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    _DB_DIR = BASE_DIR

# One-shot migration: lift an existing legacy DB into the new location.
_LEGACY_DB = BASE_DIR / "db.sqlite3"
_NEW_DB = _DB_DIR / "db.sqlite3"
if _DB_DIR != BASE_DIR and _LEGACY_DB.exists() and not _NEW_DB.exists():
    try:
        import shutil as _shutil

        _shutil.copy2(_LEGACY_DB, _NEW_DB)
    except Exception:
        pass


def _get_secret_key() -> str:
    """Resolve the Django secret key with a safe default."""
    env_key = os.environ.get("DJANGO_SECRET_KEY")
    if env_key:
        return env_key

    key_file = _DB_DIR / ".django_secret_key"
    try:
        if key_file.exists():
            existing = key_file.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        from django.core.management.utils import get_random_secret_key

        generated = get_random_secret_key()
        key_file.write_text(generated, encoding="utf-8")
        return generated
    except Exception:
        from django.core.management.utils import get_random_secret_key

        return get_random_secret_key()


SECRET_KEY = _get_secret_key()
DEBUG = _env_flag("DJANGO_DEBUG", False)
ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", "*") or ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "searchapp.apps.SearchappConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "webgui.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "searchapp.context_processors.version_context",
                "searchapp.context_processors.active_downloads_context",
                "searchapp.context_processors.arr_stack_context",
            ],
        },
    },
]

WSGI_APPLICATION = "webgui.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": _NEW_DB,
    }
}

LANGUAGE_CODE = "it-it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_DIRS = [BASE_DIR / "assets"] if (BASE_DIR / "assets").exists() else []

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS")
USE_X_FORWARDED_HOST = _env_flag("USE_X_FORWARDED_HOST", False)
CSRF_COOKIE_SECURE = _env_flag("CSRF_COOKIE_SECURE", False)
SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)

if _env_flag("SECURE_PROXY_SSL_HEADER_ENABLED", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ARR policy toggles (used by ARR runtime/config tooling)
ARR_WEBHOOK_PRIORITY_ENABLED = _env_flag("ARR_WEBHOOK_PRIORITY_ENABLED", True)
ARR_NATIVE_WEBHOOK_PRIORITY_WINDOW_SECONDS = _env_int("ARR_NATIVE_WEBHOOK_PRIORITY_WINDOW_SECONDS", 120)
ARR_SEERR_FALLBACK_DELAY_SECONDS = _env_int("ARR_SEERR_FALLBACK_DELAY_SECONDS", 20)

_ARR_LOG_DIR = PROJECT_ROOT / ".cache" / "arr"
try:
    _ARR_LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# ── VibraVid log level (read from config so Django stays in sync) ────────────────
try:
    from VibraVid.utils import config_manager as _vm_config

    _VIBRAVID_LOG_LEVEL = _vm_config.config.get("DEFAULT", "log_level", default="INFO").upper()
except Exception:
    _VIBRAVID_LOG_LEVEL = "INFO"

if not isinstance(logging.getLevelName(_VIBRAVID_LOG_LEVEL), int):
    _VIBRAVID_LOG_LEVEL = "INFO"

_VV_LOG_DIR = PROJECT_ROOT / ".cache" / "logs"
try:
    _VV_LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_VV_LOG_FILE = str(_VV_LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

# ── Logging Configuration ────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
        },
        "vibravid": {
            "format": "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "stream": "ext://sys.stderr",
        },
        "vibravid_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "vibravid",
            "filename": _VV_LOG_FILE,
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
        },
        "arr_file": {
            "class": "logging.FileHandler",
            "formatter": "verbose",
            "filename": os.path.join(
                PROJECT_ROOT,
                ".cache",
                "arr",
                datetime.now().strftime("arr_%Y%m%d_%H%M%S.log"),
            ),
            "encoding": "utf-8",
        },
    },
    "root": {
        "handlers": ["vibravid_file"],
        "level": os.environ.get("DJANGO_LOG_LEVEL", _VIBRAVID_LOG_LEVEL),
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "searchapp": {
            "handlers": ["console", "vibravid_file"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", _VIBRAVID_LOG_LEVEL),
            "propagate": False,
        },
        "ARR": {
            "handlers": ["console", "arr_file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
