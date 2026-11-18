# 29.07.26

import sys

from VibraVid.cli.bootstrap import run_cli, run_guarded


def run_tui_or_cli() -> None:
    if len(sys.argv) > 1:
        run_cli()
    else:
        from VibraVid.tui.app import main as tui_main

        tui_main()


if __name__ == "__main__":
    run_guarded(run_tui_or_cli)
