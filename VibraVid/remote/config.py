# 12.10.26

import json
import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

REMOTE_FILENAME = "remote.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8786


@dataclass(frozen=True)
class RemoteUser:
    username: str
    sites: frozenset | None = None
    server_cdm: bool = False


@dataclass(frozen=True)
class ServeSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    sites: frozenset | None = None
    users: dict = field(default_factory=dict)
    session_ttl: int = 300
    max_sessions: int = 100
    bind_session_to_ip: bool = True


@dataclass(frozen=True)
class RemoteServer:
    name: str
    url: str
    api_key: str
    server_cdm: bool = False
    send_credentials: bool = True
    allow_insecure: bool = False


def remote_config_path() -> str:
    from VibraVid.utils import config_manager

    return os.path.join(config_manager.conf_path, REMOTE_FILENAME)


def _read(path: str | None) -> dict:
    path = path or remote_config_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.error(f"Cannot read {path}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def _site_set(value) -> frozenset | None:
    if value is None:
        return None
    return frozenset(str(v).strip() for v in value if str(v).strip())


def load_serve_settings(path: str | None = None) -> ServeSettings:
    """Server side: the ``serve`` block of ``Conf/remote.json``."""
    block = _read(path).get("serve") or {}
    users = {}
    for key, entry in (block.get("users") or {}).items():
        key = str(key)
        entry = entry if isinstance(entry, dict) else {}
        users[key] = RemoteUser(
            username=str(entry.get("username") or "user"),
            sites=_site_set(entry.get("sites")),
            server_cdm=bool(entry.get("server_cdm", False)),
        )
    return ServeSettings(
        host=str(block.get("host") or DEFAULT_HOST),
        port=int(block.get("port") or DEFAULT_PORT),
        sites=_site_set(block.get("sites")),
        users=users,
        session_ttl=int(block.get("session_ttl") or 300),
        max_sessions=int(block.get("max_sessions") or 100),
        bind_session_to_ip=bool(block.get("bind_session_to_ip", True)),
    )


def load_remote_servers(path: str | None = None) -> dict[str, RemoteServer]:
    """Client side: the ``remote_services`` block of ``Conf/remote.json``."""
    block = _read(path).get("remote_services") or {}
    out = {}
    for name, entry in block.items():
        if not isinstance(entry, dict) or not entry.get("url"):
            logger.warning(f"remote_services.{name}: 'url' is required, skipped")
            continue
        out[str(name)] = RemoteServer(
            name=str(name),
            url=str(entry["url"]).rstrip("/"),
            api_key=str(entry.get("api_key") or ""),
            server_cdm=bool(entry.get("server_cdm", False)),
            send_credentials=bool(entry.get("send_credentials", True)),
            allow_insecure=bool(entry.get("allow_insecure", False)),
        )
    return out


def pick_remote_server(name: str | None, path: str | None = None) -> RemoteServer:
    """The server named *name*, or the only configured one when *name* is omitted."""
    servers = load_remote_servers(path)
    if not servers:
        raise LookupError(f"No remote server configured: add a 'remote_services' block to {path or remote_config_path()}")
    if name:
        if name not in servers:
            raise LookupError(f"Unknown remote server '{name}'. Configured: {', '.join(sorted(servers))}")
        return servers[name]
    if len(servers) > 1:
        raise LookupError(f"Several remote servers configured ({', '.join(sorted(servers))}): choose one with --server")
    return next(iter(servers.values()))
