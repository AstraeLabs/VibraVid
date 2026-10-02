# 30.07.26

from rich.console import Console

console = Console()

ANONYMOUS = "Anonymous"
ACCOUNT = "Account"
DEVICE = "Device"


def print_login(auth_type: str, user: str = "") -> None:
    """
    Print the shared two-line login banner.

    Args:
        auth_type: One of ANONYMOUS / ACCOUNT / DEVICE.
        user: Account name already in hand, if any. Never looked up here: it must cost no request.
    """
    # Emitted as a single print so the two lines can never be split apart by concurrent output.
    line = f"[cyan]Login - Type: [green]{auth_type}"
    if user:
        line += f"\n[cyan]  User: [green]{user}"
    console.print(line)
