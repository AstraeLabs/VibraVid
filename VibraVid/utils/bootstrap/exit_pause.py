import sys


def _has_own_console() -> bool:
    """True if this process owns its console (e.g. launched by double-click)."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    buf = (ctypes.c_uint * 8)()
    count = kernel32.GetConsoleProcessList(buf, 8)
    return count == 1


def pause_if_needed() -> None:
    """On Windows, pause before exit if the process owns its own console."""
    if sys.platform != "win32":
        return
    if _has_own_console():
        input("\nPress Enter to exit...")
