# 05.10.26

import os

import pytest
from rich.text import Text


@pytest.fixture
def printed(capsys):
    """What was printed since the last call, as plain text: the console the bars share forces colors off a Windows terminal, and its escape codes split a phrase like ``5 empty block(s)``."""
    return lambda: Text.from_ansi(capsys.readouterr().out).plain


@pytest.fixture(scope="session", autouse=True)
def _no_binary_downloads():
    if os.environ.get("VIBRAVID_TEST_ALLOW_DOWNLOAD") == "1":
        yield
        return

    from VibraVid.setup.binary_paths import binary_paths

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(binary_paths, "download_binary", lambda *args, **kwargs: None)
        yield
