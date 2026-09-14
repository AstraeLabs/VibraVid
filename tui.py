# 29.07.26

import sys
import traceback

from VibraVid.utils.frozen import fix_ld_library_path

fix_ld_library_path()

if __name__ == "__main__":
    from VibraVid.utils.exit_pause import pause_if_needed

    try:
        if len(sys.argv) > 1:
            from VibraVid.cli.run import main as cli_main

            cli_main()
        else:
            from VibraVid.tui.app import main as tui_main

            tui_main()
    except Exception:
        traceback.print_exc()
        pause_if_needed()
        sys.exit(1)
