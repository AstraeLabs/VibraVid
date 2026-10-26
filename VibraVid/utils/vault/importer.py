# 26.10.26

import logging
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

from rich.console import Console

from VibraVid.utils.http_client import create_client
from VibraVid.utils.upload.version import __version__
from VibraVid.utils.vault import default as default_vault_module
from VibraVid.utils.vault.local import DB_NAME, service_name

console = Console()
logger = logging.getLogger(__name__)

MAX_GROUPS_PER_REQUEST = 10
MAX_KEYS_PER_GROUP = 50
MAX_ATTEMPTS = 6
_DONE = ("added", "unchanged", "locked")


def find_dbs(path: str) -> list[Path]:
    """The vault .db at *path*, or every vault.db below it when *path* is a folder."""
    p = Path(path)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(p.rglob(DB_NAME))
    return []


def _service_of(db: Path, fallback: str | None) -> str:
    """<...>/services/<service>/vault.db names its service; any other place needs the fallback (--meta-site), else "generic"."""
    if db.parent.parent.name == "services":
        return service_name(db.parent.name)
    return service_name(fallback)


def _pending(conn: sqlite3.Connection, resend: bool, dry_run: bool) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """{(license_url, pssh): [(kid, key), ...]} of the rows still to send."""
    has_synced = "synced_at" in {row[1] for row in conn.execute("PRAGMA table_info(keys)")}
    if not has_synced and not dry_run:  # a db written before the column existed
        conn.execute("ALTER TABLE keys ADD COLUMN synced_at TEXT")
        conn.commit()
        has_synced = True

    where = " WHERE synced_at IS NULL" if has_synced and not resend else ""
    groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for kid, key, license_url, pssh in conn.execute(f"SELECT kid, key, license_url, pssh FROM keys{where} ORDER BY license_url, pssh"):
        groups[(license_url, pssh)].append((kid, key))
    return groups


def _post(client, url: str, body: dict) -> dict | None:
    """POST one import request. 429 waits Retry-After; a gateway error (the vault waking up) backs off and retries."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.post(url, json=body, timeout=120)
        except Exception as e:
            logger.error(f"vault import: request failed: {e}")
            response = None

        if response is not None and response.status_code == 200:
            return response.json()

        if response is not None and response.status_code not in (429, 502, 503, 504):
            console.print(f"[red]Import refused ({response.status_code}): {response.text[:200]}")
            return None

        wait = 2.0 * attempt
        if response is not None and response.status_code == 429:
            try:
                wait = float(response.headers.get("Retry-After", 60))
            except ValueError:
                wait = 60.0
        console.print(f"[yellow]Server busy, waiting {wait:.0f}s (attempt {attempt}/{MAX_ATTEMPTS})")
        time.sleep(wait)
    return None


def import_to_server(path: str, *, service: str | None = None, resend: bool = False, dry_run: bool = False) -> dict[str, int]:
    """Send the keys of the local vault .db at *path* (a file, or a folder of vault.db files) to the default vault server."""
    totals = {"added": 0, "unchanged": 0, "locked": 0, "error": 0, "pending": 0}
    base_url = default_vault_module.VAULT_URL.rstrip("/")
    if not base_url:
        console.print("[red]No server: set url in [DRM.vault.default] of config.toml")
        return totals

    dbs = find_dbs(path)
    if not dbs:
        console.print(f"[red]No vault database found at {path}")
        return totals

    headers = {"Content-Type": "application/json", "User-Agent": f"VibraVid/{__version__}"}
    if default_vault_module.TOKEN:
        headers["Authorization"] = f"Bearer {default_vault_module.TOKEN}"
    client = None if dry_run else create_client(headers=headers, http2=True)

    try:
        for db in dbs:
            svc = _service_of(db, service)
            conn = sqlite3.connect(db, timeout=30)
            try:
                groups = _pending(conn, resend, dry_run)
                count = sum(len(v) for v in groups.values())
                console.print(f"[cyan]{db} -> service [green]{svc}[cyan]: {count} key(s) to send in {len(groups)} group(s)")
                totals["pending"] += count
                if dry_run or not groups:
                    continue

                # (license_url, pssh, [(kid, key), ...] up to 50): the unit the server saves in one transaction
                chunks = [
                    (lic, pssh, rows[i : i + MAX_KEYS_PER_GROUP])
                    for (lic, pssh), rows in groups.items()
                    for i in range(0, len(rows), MAX_KEYS_PER_GROUP)
                ]
                for start in range(0, len(chunks), MAX_GROUPS_PER_REQUEST):
                    batch = chunks[start : start + MAX_GROUPS_PER_REQUEST]
                    body = {
                        "service": svc,
                        "groups": [{"license_url": lic, "pssh": pssh, "keys": [{"kid": k, "key": v} for k, v in rows]} for lic, pssh, rows in batch],
                    }
                    result = _post(client, f"{base_url}/v1/import", body)
                    if result is None:
                        totals["error"] += sum(len(rows) for _, _, rows in batch)
                        continue

                    for (lic, pssh, rows), group in zip(batch, result.get("groups", []), strict=False):
                        status = {r.get("kid"): r.get("status") for r in group.get("results", [])}
                        for kid, _ in rows:
                            s = status.get(kid)
                            if s in _DONE:
                                totals[s] += 1
                                conn.execute(
                                    "UPDATE keys SET synced_at = datetime('now') WHERE kid = ? AND license_url = ? AND pssh = ?",
                                    (kid, lic, pssh),
                                )
                            else:
                                totals["error"] += 1
                    conn.commit()
            finally:
                conn.close()
    finally:
        if client is not None:
            client.close()

    verb = "to send" if dry_run else f"sent: {totals['added']} added, {totals['unchanged']} already stored, {totals['locked']} locked, {totals['error']} failed"
    console.print(f"[green]Vault import done, {totals['pending']} key(s) pending, {verb}")
    return totals


def run_cli(path: str, service: str | None, resend: bool, dry_run: bool) -> int:
    """Entry point of `--vault-import PATH`: exit code 0 when no key failed."""
    if not os.path.exists(path):
        console.print(f"[red]Not found: {path}")
        return 1
    totals = import_to_server(path, service=service, resend=resend, dry_run=dry_run)
    return 0 if totals["error"] == 0 else 1
