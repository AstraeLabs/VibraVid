# 30.07.26

import threading
import time
from contextlib import contextmanager

from rich.console import Console
from rich.markup import escape

console = Console(highlight=False)
_recording = threading.local()


class Login:
    ANONYMOUS = "Anonymous"
    ACCOUNT = "Account"
    DEVICE = "Device"

    _cached_seen: set[str] = set()
    _open = False
    _banner_shown = False

    @staticmethod
    @contextmanager
    def recording(events: list):
        """Collect the calls made by this thread into *events* instead of printing them (a remote server: the client shows them)."""
        previous = getattr(_recording, "events", None)
        _recording.events = events
        try:
            yield
        finally:
            _recording.events = previous

    @staticmethod
    def _recorded(kind: str, *args) -> bool:
        events = getattr(_recording, "events", None)
        if events is None:
            return False
        events.append([kind, *args])
        return True

    @staticmethod
    def replay(events: list) -> None:
        """Print calls recorded elsewhere exactly as if they had been made here."""
        for kind, *args in events or []:
            if kind == "show":
                Login.show(args[0], args[1], args[2])
            elif kind == "refresh_try":
                Login.refresh_try()
            elif kind == "refresh_cached":
                Login.refresh_cached(Login._local_cache_path(args[0]))
            elif kind == "refresh_ok":
                Login.refresh_ok(args[0])
            elif kind == "refresh_failed":
                Login.refresh_failed(args[0])

    @staticmethod
    def _local_cache_path(path: str) -> str:
        """A server reports its cache files as ``remote-cache://<service>/<name>.json``; here that is the client's own file."""
        from VibraVid.utils import disk_cache
        from VibraVid.utils.scope import CACHE_PATH_PREFIX

        if not str(path).startswith(CACHE_PATH_PREFIX):
            return str(path)
        service, _, filename = str(path)[len(CACHE_PATH_PREFIX):].partition("/")
        return disk_cache.cache_path(service, filename[:-5] if filename.endswith(".json") else filename)

    @staticmethod
    def _emit(text: str, **kwargs) -> None:
        if not Login._open:
            console.print()
            Login._open = True
        console.print(text, **kwargs)

    @staticmethod
    def _close() -> None:
        if Login._open:
            console.print()
            Login._open = False

    @staticmethod
    def _result_done() -> None:
        if Login._banner_shown:
            Login._close()

    @staticmethod
    def show(auth_type: str, user: str = "", details: dict | None = None) -> None:
        """Show the login type and user (if any) in the console, with optional extra details."""
        if Login._recorded("show", auth_type, user, details or {}):
            return
        rows = {"Login type": auth_type, "User": user, **(details or {})}
        Login._emit("\n".join(f"[cyan][+][/cyan] [white]{label}:[/white] [green]{value}[/green]" for label, value in rows.items() if value))
        Login._banner_shown = True
        Login._close()

    @staticmethod
    def refresh_try() -> None:
        """A token refresh is starting. Call only when a request is actually about to be made."""
        if Login._recorded("refresh_try"):
            return
        Login._emit("[cyan][+][/cyan] [white]Try refresh token...[/white]")

    @staticmethod
    def refresh_cached(path: str) -> None:
        """A still valid token was reused from the cache file at `path`, so no refresh was needed. Printed once per file and run."""
        if Login._recorded("refresh_cached", str(path)):
            return
        key = str(path)
        if key in Login._cached_seen:
            return
        Login._cached_seen.add(key)
        Login._emit(f"[cyan][+][/cyan] [white]Try refresh token...[/white]\n[cyan]{escape('[/]')}[/cyan] [white]Use cached token[/white] [green]({escape(key)})[/green]", soft_wrap=True)
        Login._result_done()

    @staticmethod
    def refresh_ok(valid_until: float | None = None) -> None:
        """The refresh worked. `valid_until` (epoch seconds) is the new token's expiry, only when the service tells it."""
        if Login._recorded("refresh_ok", valid_until):
            return
        when = f" [green](valid until {time.strftime('%d/%m/%Y', time.localtime(valid_until))})[/green]" if valid_until else ""
        Login._emit(f"[green][+][/green] [white]Success[/white]{when}")
        Login._result_done()

    @staticmethod
    def refresh_failed(reason: str = "") -> None:
        """The refresh did not work; `reason` is a short hint (status code, error name)."""
        if Login._recorded("refresh_failed", reason):
            return
        Login._emit(f"[red][-][/red] [white]Failed[/white] [red]({reason})[/red]" if reason else "[red][-][/red] [white]Failed[/white]")
        Login._result_done()
