# 01.11.26

import hmac
import logging
import os
import threading
import time
from functools import wraps

from django.http import JsonResponse

logger = logging.getLogger(__name__)

SESSION_KEY = "vibravid_authenticated"

# Machine-to-machine entry points that carry their own secret/key (see api_bot, api_remote, arr views).
EXEMPT_PREFIXES = (
    "/static/",
    "/login/",
    "/api/arr/webhook/",
    "/api/bot/",
    "/api/remote/",
)

# Used by the Telegram sidecar, which authenticates with X-VibraVid-Token instead of a session.
BOT_TOKEN_PATHS = ("/api/get-downloads/",)


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def gui_auth_enabled() -> bool:
    return bool(_env("VIBRAVID_USERNAME") and os.environ.get("VIBRAVID_PASSWORD"))


def service_upload_allowed() -> bool:
    return _env("VIBRAVID_ALLOW_SERVICE_UPLOAD").lower() in {"1", "true", "yes", "on"}


def check_credentials(username: str, password: str) -> bool:
    expected_user = _env("VIBRAVID_USERNAME")
    expected_password = os.environ.get("VIBRAVID_PASSWORD") or ""
    # Evaluate both comparisons so the response time does not reveal which one failed.
    user_ok = hmac.compare_digest((username or "").encode(), expected_user.encode())
    password_ok = hmac.compare_digest((password or "").encode(), expected_password.encode())
    return bool(expected_user and expected_password and user_ok and password_ok)


def bot_token_valid(request) -> bool:
    secret = _env("VIBRAVID_BOT_SECRET")
    supplied = request.headers.get("X-VibraVid-Token", "")
    return bool(secret and supplied and hmac.compare_digest(supplied.encode(), secret.encode()))


class LoginThrottle:
    """Per-address brake on failed logins (in memory, resets on restart)."""

    MAX_FAILURES = 5
    WINDOW_SECONDS = 300

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def _recent(self, address: str) -> list[float]:
        cutoff = time.monotonic() - self.WINDOW_SECONDS
        recent = [stamp for stamp in self._failures.get(address, []) if stamp > cutoff]
        self._failures[address] = recent
        return recent

    def is_blocked(self, address: str) -> bool:
        with self._lock:
            return len(self._recent(address)) >= self.MAX_FAILURES

    def record_failure(self, address: str) -> None:
        with self._lock:
            self._recent(address).append(time.monotonic())

    def reset(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


login_throttle = LoginThrottle()


def log_startup_warning() -> None:
    if not gui_auth_enabled():
        logger.warning(
            "GUI login is disabled: anyone who can reach this port controls the app. "
            "Set VIBRAVID_USERNAME and VIBRAVID_PASSWORD to require a login."
        )


def require_service_upload(view):
    """Refuse the view unless ``VIBRAVID_ALLOW_SERVICE_UPLOAD`` is on (it installs/executes code)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not service_upload_allowed():
            return JsonResponse({
                "success": False,
                "error": "Disabled: installing or importing provider code can run arbitrary code on the "
                         "server. Set VIBRAVID_ALLOW_SERVICE_UPLOAD=1 to enable it.",
            }, status=403)
        return view(request, *args, **kwargs)

    return wrapper
