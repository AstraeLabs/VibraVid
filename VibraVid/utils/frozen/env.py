# 04.07.26

import os
import sys


def fix_ld_library_path():
    """Restore the original LD_LIBRARY_PATH when running as a PyInstaller onefile binary on Linux."""
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        lp_orig = os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        if lp_orig is not None:
            os.environ["LD_LIBRARY_PATH"] = lp_orig
        else:
            os.environ.pop("LD_LIBRARY_PATH", None)
