# 26.11.24
# ruff: noqa: E402

import sys
import traceback

from VibraVid.utils.frozen import fix_ld_library_path

fix_ld_library_path()

from VibraVid.cli.run import main
from VibraVid.utils.exit_pause import pause_if_needed

if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        pause_if_needed()
        sys.exit(1)
