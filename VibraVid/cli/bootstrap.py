# 05.10.26

import sys
import traceback
from collections.abc import Callable

from VibraVid.utils.exit_pause import pause_if_needed
from VibraVid.utils.frozen import fix_ld_library_path


def run_guarded(entry: Callable[[], None]) -> None:
    """Run *entry* after fixing the library path; an unhandled error prints the traceback, pauses if needed and exits with 1."""
    fix_ld_library_path()
    try:
        entry()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        pause_if_needed()
        sys.exit(1)


def run_cli() -> None:
    from VibraVid.cli.run import main

    main()


def main() -> None:
    """``vibravid`` console script / ``manual.py`` / ``python -m VibraVid``."""
    run_guarded(run_cli)
