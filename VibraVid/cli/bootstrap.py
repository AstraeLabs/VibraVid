# 05.10.26

import sys
import traceback
from collections.abc import Callable

from VibraVid.utils.exit_pause import pause_if_needed
from VibraVid.utils.frozen import fix_ld_library_path


def _is_download_cancelled(exc: BaseException) -> bool:
    """True for a user-initiated abort (lazy import: bootstrap must stay light at startup)."""
    try:
        from VibraVid.core.downloader.base import DownloadCancelled

        if isinstance(exc, DownloadCancelled):
            return True
    except Exception:
        pass
    return type(exc).__name__ == "DownloadCancelled"


def run_guarded(entry: Callable[[], None]) -> None:
    """Run *entry* after fixing the library path; an unhandled error prints the traceback, pauses if needed and exits with 1."""
    fix_ld_library_path()
    try:
        entry()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as exc:
        if _is_download_cancelled(exc):
            # Double Ctrl+C: a voluntary abort, not a crash — no traceback.
            print("\nDownload cancelled by user.")
            sys.exit(130)
        traceback.print_exc()
        pause_if_needed()
        sys.exit(1)


def run_cli() -> None:
    from VibraVid.cli.run import main

    main()


def main() -> None:
    """``vibravid`` console script / ``manual.py`` / ``python -m VibraVid``."""
    run_guarded(run_cli)
