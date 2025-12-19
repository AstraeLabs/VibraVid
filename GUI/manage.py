#!/usr/bin/env python3
import os
import sys


# Fix PYTHONPATH
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()