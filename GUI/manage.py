#!/usr/bin/env python3
import os
import subprocess
import sys

# Fix PYTHONPATH
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")

    is_server = len(sys.argv) > 1 and sys.argv[1] in {"runserver", "runserver_plus"}
    if is_server and os.environ.get("RUN_MAIN") != "true":
        subprocess.run([sys.executable, __file__, "migrate", "--noinput"], check=True, env={**os.environ, "VIBRAVID_SKIP_PRE_RUN_HOOKS": "1"})

    if "RUN_MAIN" not in os.environ and "VIBRAVID_SKIP_PRE_RUN_HOOKS" not in os.environ:
        print("Running pre-run hooks...")
        from VibraVid.utils.hooks import execute_hooks

        execute_hooks("pre_run")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
