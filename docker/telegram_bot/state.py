# 26.07.26

import json
import logging
import os
from collections import OrderedDict, deque
from pathlib import Path

from telethon import TelegramClient

CONFIG_PATH = Path(os.getenv("VIBRAVID_CONFIG", "/app/Conf/config.json"))
STATE_DIR = Path(os.getenv("STATE_DIR", "/app/tgstate"))
PAGE_SIZE = 15
EP_PAGE_SIZE = 30
EP_COLS = 5
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)

log = logging.getLogger("telegram_bot")
_LOGBUF: deque[str] = deque(maxlen=500)



class _RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOGBUF.append(self.format(record))
        except Exception:
            pass



_ring = _RingHandler()
_ring.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
_ring.setLevel(logging.INFO)
logging.getLogger().addHandler(_ring)
logging.getLogger("telethon").setLevel(logging.WARNING)



class Config:
    """Config with hot-reload: re-reads config.json when its mtime changes"""
    def __init__(self) -> None:
        self._mtime = 0.0
        self._tg: dict = {}
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        try:
            mtime = CONFIG_PATH.stat().st_mtime
        except OSError:
            mtime = 0.0
        if not force and mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            with CONFIG_PATH.open(encoding="utf-8") as fh:
                data = json.load(fh)
            self._tg = data.get("TELEGRAM", {}) or {}
        except (OSError, ValueError):
            log.exception("config.json unreadable (%s), using env only", CONFIG_PATH)
            self._tg = {}

    def _get(self, key: str, env: str, default):
        val = self._tg.get(key)
        if val not in (None, "", []):
            return val
        val = os.getenv(env, "").strip()
        return val if val else default

    @property
    def enabled(self) -> bool:
        return str(self._get("enabled", "TG_ENABLED", "true")).lower() in ("1", "true", "yes")

    @property
    def bot_token(self) -> str:
        return os.getenv("TG_BOT_TOKEN", "").strip()

    @property
    def api_id(self) -> int:
        try:
            return int(os.getenv("TG_API_ID", "0").strip() or "0")
        except ValueError:
            return 0

    @property
    def api_hash(self) -> str:
        return os.getenv("TG_API_HASH", "").strip()

    @property
    def allowed_users(self) -> set[int]:
        raw = os.getenv("TG_ALLOWED_USERS", "")
        allowed = set()
        for entry in (x for x in raw.replace(" ", "").split(",") if x):
            try:
                allowed.add(int(entry))
            except ValueError:
                log.warning("Ignoring invalid TG_ALLOWED_USERS entry: %r", entry)
        return allowed

    @property
    def restricted(self) -> bool:
        """An allow-list is configured. A malformed one stays a restriction: it must never turn into "everyone is allowed"."""
        return bool(os.getenv("TG_ALLOWED_USERS", "").replace(" ", "").strip(","))

    @property
    def max_results(self) -> int:
        try:
            return int(self._get("max_results", "TG_MAX_RESULTS", "8"))
        except ValueError:
            return 8

    @property
    def gui_url(self) -> str:
        return str(self._get("gui_url", "TG_GUI_URL", "http://vibravid:8000")).rstrip("/")



cfg = Config()
client: TelegramClient | None = None
_REMOTE: OrderedDict[str, list[dict]] = OrderedDict()
DEFAULT_SITE = "__all__"
PREFS_FILE = STATE_DIR / "prefs.json"
_prefs: dict[str, str] = {}
_last_query: dict[int, str] = {}
_awaiting_scarica_query: set[int] = set()
_awaiting_site_search: dict[int, str] = {}


_SITEOPTS_STORE: OrderedDict[str, list[dict]] = OrderedDict()
_site_opts: list[dict] = []
_site_opts_ts: float = 0.0
SITE_OPTS_TTL = 300

_pending: dict[str, dict] = {}
_CANCEL_TOKENS: "OrderedDict[str, str]" = OrderedDict()
PENDING_TIMEOUT = 6 * 3600
FINALIZE_GRACE_SECONDS = 180
SETTLE_SECONDS = 90


__all__ = [
    'CONFIG_PATH', 'STATE_DIR', 'PAGE_SIZE', 'EP_PAGE_SIZE', 'EP_COLS', 'log',
    '_LOGBUF', '_RingHandler', '_ring', 'Config', 'cfg', 'client', '_REMOTE',
    'DEFAULT_SITE', 'PREFS_FILE', '_prefs', '_last_query', '_awaiting_scarica_query',
    '_awaiting_site_search', '_SITEOPTS_STORE', '_site_opts', '_site_opts_ts',
    'SITE_OPTS_TTL', '_pending', '_CANCEL_TOKENS', 'PENDING_TIMEOUT',
    'FINALIZE_GRACE_SECONDS', 'SETTLE_SECONDS',
]
