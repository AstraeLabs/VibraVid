# 09.06.26
# ruff: noqa: E402

import os
import sys
import time

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader import Generic_Downloader
from VibraVid.core.ui.tracker import context_tracker


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")
context_tracker.force_livemux = True


SOURCES = [
    {"url": "<url>", "key": "<key>", "type": "video"},
    {"url": "<url>", "key": "<key>", "language": "en", "type": "audio"},
]

t0 = time.monotonic()
generic_process = Generic_Downloader(
    sources=SOURCES, 
    output_path=rf".\Video\Custom.{conf_extension}"
)


out_path, need_stop, error = generic_process.start()
print(f"out={out_path} need_stop={need_stop} error={error} elapsed={time.monotonic() - t0:.2f}s")
