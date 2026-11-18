# 05.10.26

import atexit
import os
import shutil
import tempfile

_db_dir = tempfile.mkdtemp(prefix="vibravid_gui_tests_")
atexit.register(shutil.rmtree, _db_dir, ignore_errors=True)  # don't leave a folder in Temp per run
os.environ["DJANGO_DB_DIR"] = _db_dir
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")
os.environ.pop("VIBRAVID_BOT_SECRET", None)


def pytest_sessionfinish(session, exitstatus):
    """Close the SQLite connection first: Windows can't delete a database file that is still open."""
    try:
        from django.db import connections

        connections.close_all()
    except Exception:
        pass
    shutil.rmtree(_db_dir, ignore_errors=True)
