# 12.10.26

import builtins
import contextlib
import io
import logging
import os
import re
import sys
import tempfile
import threading
import types

from .models import RemoteError

logger = logging.getLogger(__name__)


_INSTANCE_NAME = re.compile(r"^_[a-z0-9_]*(client|api)[a-z0-9_]*$")
_REGISTRY_NAME = re.compile(r"^_[a-z0-9_]*(client|by_region|cache)[a-z0-9_]*$")
EXPLICIT_ATTRIBUTE = "_identity_state"

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}
_last_scope: dict[str, str] = {}

_forgetters: list = []
_guards_installed = False
_job_state = threading.local()


def site_lock(site: str) -> threading.Lock:
    """One lock per service: while it is held its module globals belong to a single identity."""
    with _guard:
        return _locks.setdefault(site, threading.Lock())


def _service_module(site: str):
    from VibraVid.services._base.site_loader import load_search_functions

    lazy = load_search_functions().get(f"{site}_search")
    try:
        return lazy.get_module() if lazy else None
    except Exception:
        return None


def _service_modules(site: str) -> list:
    root = _service_module(site)
    if root is None:
        return []
    prefix = root.__name__
    return [m for name, m in list(sys.modules.items()) if m is not None and (name == prefix or name.startswith(prefix + "."))]


def _owned_by(value, prefix: str) -> bool:
    return type(value).__module__.startswith(prefix)


def reset_identity_state(site: str) -> int:
    """Drop the identity-bound module globals of *site*; returns how many were reset."""
    modules = _service_modules(site)
    if not modules:
        return 0
    
    prefix = min((m.__name__ for m in modules), key=len)
    count = 0
    for module in modules:
        explicit = getattr(module, EXPLICIT_ATTRIBUTE, None)
        for attr, value in list(vars(module).items()):
            if value is None:
                continue
            if explicit is not None:
                if attr not in explicit:
                    continue
            elif isinstance(value, dict):
                if not _REGISTRY_NAME.match(attr):
                    continue
            elif isinstance(value, (type, types.ModuleType, str, int, float, bool, tuple)) or callable(value):
                continue
            elif not (_INSTANCE_NAME.match(attr) and _owned_by(value, prefix)):
                continue

            if isinstance(value, dict):
                if value:
                    value.clear()
                    count += 1
            else:
                setattr(module, attr, None)
                count += 1
    if count:
        logger.info(f"{site}: reset {count} identity-bound module value(s) for the next client")
    return count


def on_forget(fn) -> None:
    """Register *fn(scope)*: it must drop whatever the caller keeps in memory for that identity (e.g. cached scrapers)."""
    _forgetters.append(fn)


def forget(site: str, scope: str) -> None:
    """The request is over: nothing about the client stays on the server (module state, cached scrapers)."""
    reset_identity_state(site)
    _last_scope.pop(site, None)
    for fn in _forgetters:
        try:
            fn(scope)
        except Exception: 
            logger.debug("forgetter failed", exc_info=True)


def prepare(site: str, scope: str) -> None:
    """Call with the site lock held, before running as *scope*: clears the previous identity's module state if it changed."""
    if _last_scope.get(site) != scope:
        reset_identity_state(site)
        _last_scope[site] = scope


def current_job():
    """The login job whose thread this is, or ``None``."""
    return getattr(_job_state, "job", None)


@contextlib.contextmanager
def bind_job(job):
    previous = current_job()
    _job_state.job = job
    try:
        yield
    finally:
        _job_state.job = previous


def _write_roots() -> list[str]:
    """Where a server may still write while serving a client: scratch space and its own logs, never user data."""
    from VibraVid.utils import config_manager

    cache = os.path.join(config_manager.base_path or ".", ".cache")
    roots = [tempfile.gettempdir(), os.path.join(cache, "logs"), os.path.join(cache, "arr"), os.path.join(cache, "services", "_site_loader")]
    return [os.path.normcase(os.path.abspath(r)) for r in roots]


def _write_allowed(path) -> bool:
    try:
        target = os.path.normcase(os.path.abspath(os.fspath(path)))
    except TypeError:
        return True  # a file descriptor, not a path
    return any(target == root or target.startswith(root + os.sep) for root in _write_roots())


def _strip_markup(text) -> str:
    return re.sub(r"\[/?[^\]]*\]", "", str(text)).strip()


def install_guards() -> None:
    """Make a remote request fail fast, with a clear message, on the things a server cannot do for a client."""
    global _guards_installed
    if _guards_installed:
        return
    _guards_installed = True

    import webbrowser

    from rich.console import Console
    from rich.prompt import PromptBase

    from VibraVid.utils.scope import current_overlay

    original_ask = PromptBase.ask.__func__
    original_input = builtins.input
    original_open = builtins.open
    original_print = builtins.print
    original_console_print = Console.print

    def ask(cls, prompt="", *args, **kwargs):
        job = current_job()
        if job is not None:
            return job.ask(cls, prompt, **kwargs)
        if current_overlay() is not None:
            raise RemoteError(f"the service asked for input a server cannot give: '{_strip_markup(prompt)[:80]}'", 422)
        return original_ask(cls, prompt, *args, **kwargs)

    def ask_input(prompt=""):
        job = current_job()
        if job is not None:
            return job.ask_text(str(prompt))
        if current_overlay() is not None:
            raise RemoteError(f"the service asked for input a server cannot give: '{str(prompt)[:80]}'", 422)
        return original_input(prompt)

    def guarded_open(file, mode="r", *args, **kwargs):
        if isinstance(mode, str) and any(c in mode for c in "wax+") and current_overlay() is not None and not _write_allowed(file):
            raise PermissionError(f"remote server: refusing to write {file} (a server stores nothing of a client)")
        return original_open(file, mode, *args, **kwargs)

    def console_print(self, *objects, **kwargs):
        job = current_job()
        if job is None:
            return original_console_print(self, *objects, **kwargs)
        buffer = io.StringIO()
        capture = Console(file=buffer, force_terminal=True, color_system="standard", width=min(self.width or 100, 120))
        original_console_print(capture, *objects, **kwargs)  # the real printer: ours would capture this too, forever
        job.say(buffer.getvalue())
        return None

    def job_print(*args, sep=" ", end="\n", file=None, flush=False):
        job = current_job()
        if job is None or file not in (None, sys.stdout, sys.stderr):
            return original_print(*args, sep=sep, end=end, file=file, flush=flush)
        job.say((sep or " ").join(str(a) for a in args) + (end if end is not None else "\n"))
        return None

    def opener(original):
        def open_url(url, *args, **kwargs):
            job = current_job()
            if job is None:
                return original(url, *args, **kwargs)
            job.say(f"Open this link in your browser: {url}\n")
            return True

        return open_url

    PromptBase.ask = classmethod(ask)
    builtins.input = ask_input
    builtins.open = guarded_open
    builtins.print = job_print
    io.open = guarded_open
    Console.print = console_print
    webbrowser.open = opener(webbrowser.open)
    webbrowser.open_new = opener(webbrowser.open_new)
    webbrowser.open_new_tab = opener(webbrowser.open_new_tab)
