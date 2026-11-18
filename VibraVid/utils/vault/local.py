# 26.10.26

import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone

from VibraVid.utils import disk_cache
from VibraVid.utils.vault.base import BaseVault

logger = logging.getLogger(__name__)

DB_NAME = "vault.db"
_GENERIC_SERVICE = "generic"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
    kid         TEXT NOT NULL,
    key         TEXT NOT NULL,
    license_url TEXT NOT NULL,
    pssh        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    synced_at   TEXT,
    PRIMARY KEY (kid, license_url, pssh)
);
CREATE INDEX IF NOT EXISTS keys_pssh ON keys (pssh);
"""


def service_name(value: str | None) -> str:
    """A service name as the vault server accepts it: lowercase letters, digits and dashes ("generic" when empty)."""
    return re.sub(r"[^a-z0-9-]+", "", (value or "").strip().lower()) or _GENERIC_SERVICE


def open_db(path: str) -> sqlite3.Connection:
    """Open (creating it if needed) a vault .db with the keys table in place."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _norm(hex_value: str) -> str:
    return hex_value.replace("-", "").strip().lower()


class LocalVault(BaseVault):
    def __init__(self, *, name: str = "local"):
        super().__init__(name=name)
        self._conns: dict[str, sqlite3.Connection] = {}
        self._guard = threading.Lock()

    @property
    def is_connected(self) -> bool:
        return True

    @property
    def is_writable(self) -> bool:
        return True

    @staticmethod
    def _service() -> str:
        from VibraVid.core.ui.tracker import context_tracker

        return service_name(getattr(context_tracker, "site_name", None))

    def db_path(self, service: str | None = None) -> str:
        return os.path.join(disk_cache._service_dir(service or self._service()), DB_NAME)

    def _conn(self) -> sqlite3.Connection | None:
        """The connection of the current service, or None while a remote request is served (its cache is in memory)."""
        service = self._service()
        if disk_cache._remote(service):
            return None

        path = self.db_path(service)
        conn = self._conns.get(path)
        if conn is None:
            conn = self._conns[path] = open_db(path)
        return conn

    def close(self) -> None:
        with self._guard:
            for conn in self._conns.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._conns.clear()

    def set_keys(self, keys_list: list[str], license_url: str, pssh: str = None, kid_to_label: dict | None = None) -> int:
        """Insert the keys not stored yet under (kid, license, pssh); a stored row is never overwritten. Returns the number added."""
        rows = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        license_clean = self._clean_license_url(license_url) if license_url else "generic"
        for key_str in keys_list or []:
            if ":" not in key_str:
                continue
            kid, key = key_str.split(":", 1)
            rows.append((_norm(kid), _norm(key), license_clean, pssh or "", now))

        if not rows:
            return 0

        with self._guard:
            conn = self._conn()
            if conn is None:
                return 0
            before = conn.total_changes
            conn.executemany("INSERT OR IGNORE INTO keys (kid, key, license_url, pssh, created_at) VALUES (?, ?, ?, ?, ?)", rows)
            conn.commit()
            added = conn.total_changes - before

        logger.info(f"[{self.name}] added {added}/{len(rows)} key(s) to {self.db_path()}")
        return added

    def get_keys_by_kids(self, license_url: str | None, kids: list[str], pssh: str = None) -> list[str]:
        """Keys for the KIDs as "kid:key". The license is not a filter: a KID identifies its key."""
        if not kids:
            return []

        wanted = [_norm(k) for k in kids]
        with self._guard:
            conn = self._conn()
            if conn is None:
                return []
            marks = ",".join("?" * len(wanted))
            rows = conn.execute(f"SELECT kid, key FROM keys WHERE kid IN ({marks}) ORDER BY created_at DESC, rowid DESC", wanted).fetchall()

        found: dict[str, str] = {}
        for kid, key in rows:
            found.setdefault(kid, key)  # newest row wins
        return [f"{kid}:{key}" for kid, key in found.items()]

    def get_keys_by_pssh(self, license_url: str, pssh: str) -> list[str]:
        if not pssh:
            return []

        with self._guard:
            conn = self._conn()
            if conn is None:
                return []
            rows = conn.execute("SELECT kid, key FROM keys WHERE pssh = ? ORDER BY created_at DESC, rowid DESC", (pssh,)).fetchall()
        return [f"{kid}:{key}" for kid, key in rows]

    def report_wrong_key(self, kid: str, key: str, license_url: str, pssh: str | None = None) -> bool:
        """A wrong key is not worth keeping: drop that exact row so the next run asks the CDM again."""
        with self._guard:
            conn = self._conn()
            if conn is None:
                return False
            cur = conn.execute("DELETE FROM keys WHERE kid = ? AND key = ?", (_norm(kid), _norm(key)))
            conn.commit()
            return cur.rowcount > 0

    def replace_key(self, kid: str, new_key: str, old_key: str, license_url: str, pssh: str | None = None) -> bool:
        """Swap *old_key* for *new_key* on every row of the KID; a KID with no row just gets the new key stored."""
        with self._guard:
            conn = self._conn()
            if conn is None:
                return False
            cur = conn.execute(
                "UPDATE keys SET key = ?, synced_at = NULL WHERE kid = ? AND key = ?", (_norm(new_key), _norm(kid), _norm(old_key))
            )
            conn.commit()
            if cur.rowcount > 0:
                return True
        return self.set_keys([f"{kid}:{new_key}"], license_url, pssh) > 0


local_vault = LocalVault()
