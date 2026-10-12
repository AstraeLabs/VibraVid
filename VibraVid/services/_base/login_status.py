# 30.07.26

import time

from rich.console import Console
from rich.markup import escape

console = Console(highlight=False)


class Login:
    ANONYMOUS = "Anonymous"
    ACCOUNT = "Account"
    DEVICE = "Device"

    _cached_seen: set[str] = set()
    # The messages of one login are printed by separate calls (refresh steps, then the banner): the block is opened with a
    # blank line by the first of them and closed with one after the banner. Once a banner was shown, a refresh that
    # comes later (mid-run) is a block of its own, closed after its result.
    _open = False
    _banner_shown = False

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
        rows = {"Login type": auth_type, "User": user, **(details or {})}
        Login._emit("\n".join(f"[cyan][+][/cyan] [white]{label}:[/white] [green]{value}[/green]" for label, value in rows.items() if value))
        Login._banner_shown = True
        Login._close()

    @staticmethod
    def refresh_try() -> None:
        """A token refresh is starting. Call only when a request is actually about to be made."""
        Login._emit("[cyan][+][/cyan] [white]Try refresh token...[/white]")

    @staticmethod
    def refresh_cached(path: str) -> None:
        """A still valid token was reused from the cache file at `path`, so no refresh was needed. Printed once per file and run."""
        key = str(path)
        if key in Login._cached_seen:
            return
        Login._cached_seen.add(key)
        Login._emit(f"[cyan][+][/cyan] [white]Try refresh token...[/white]\n[cyan]{escape('[/]')}[/cyan] [white]Use cached token[/white] [green]({escape(key)})[/green]", soft_wrap=True)
        Login._result_done()

    @staticmethod
    def refresh_ok(valid_until: float | None = None) -> None:
        """The refresh worked. `valid_until` (epoch seconds) is the new token's expiry, only when the service tells it."""
        when = f" [green](valid until {time.strftime('%d/%m/%Y', time.localtime(valid_until))})[/green]" if valid_until else ""
        Login._emit(f"[green][+][/green] [white]Success[/white]{when}")
        Login._result_done()

    @staticmethod
    def refresh_failed(reason: str = "") -> None:
        """The refresh did not work; `reason` is a short hint (status code, error name)."""
        Login._emit(f"[red][-][/red] [white]Failed[/white] [red]({reason})[/red]" if reason else "[red][-][/red] [white]Failed[/white]")
        Login._result_done()
