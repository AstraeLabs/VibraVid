# 05.10.26

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _no_binary_downloads():
    if os.environ.get("VIBRAVID_TEST_ALLOW_DOWNLOAD") == "1":
        yield
        return

    from VibraVid.setup.binary_paths import binary_paths

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(binary_paths, "download_binary", lambda *args, **kwargs: None)
        yield
