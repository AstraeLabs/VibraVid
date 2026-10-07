# 06.10.26

import os
import re
import shutil

BUILTIN_SERVICES = frozenset({
    "animeunity",
    "animeworld",
    "crunchyroll",
    "discovery",
    "discoveryplus",
    "dmax",
    "foodnetwork",
    "homegardentv",
    "la7",
    "mediasetinfinity",
    "monochrome",
    "nove",
    "plutotv",
    "raiplay",
    "realtime",
    "streamingcommunity",
    "tubitv",
})

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


class ServiceRemovalError(Exception):
    """Refusal to remove a service; ``status`` is the HTTP status the view answers with."""
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def services_dir() -> str:
    """Absolute path of ``VibraVid/services`` (the folder the upload installs into)."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(project_root, "VibraVid", "services")


def removable_services() -> list[str]:
    """Names of the uploaded (non built-in) service folders that currently exist, sorted."""
    base = services_dir()
    if not os.path.isdir(base):
        return []

    names = []
    for entry in sorted(os.listdir(base)):
        full = os.path.join(base, entry)
        if entry.startswith(("_", ".")) or entry in BUILTIN_SERVICES or not _NAME_RE.fullmatch(entry):
            continue
        if os.path.islink(full) or not os.path.isfile(os.path.join(full, "__init__.py")):
            continue
        names.append(entry)
    return names


def remove_service(name) -> str:
    """Delete the uploaded service folder ``name`` and return its name; raises ``ServiceRemovalError`` otherwise."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name.strip()):
        raise ServiceRemovalError(400, "Nome del servizio non valido.")
    name = name.strip()

    if name in BUILTIN_SERVICES:
        raise ServiceRemovalError(403, f"'{name}' è un servizio incluso nel progetto e non può essere rimosso.")

    base = os.path.realpath(services_dir())
    target = os.path.join(services_dir(), name)
    if os.path.islink(target):
        raise ServiceRemovalError(403, f"'{name}' è un collegamento simbolico: non viene rimosso.")
    if name not in removable_services():
        raise ServiceRemovalError(404, f"Servizio '{name}' non trovato tra quelli caricati.")
    if os.path.dirname(os.path.realpath(target)) != base:
        raise ServiceRemovalError(403, f"'{name}' non si trova nella cartella dei servizi.")

    shutil.rmtree(target)
    return name
