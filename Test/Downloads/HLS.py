# 23.06.24
# ruff: noqa: E402


import os
import sys
import time

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader import HLS_Downloader
from VibraVid.core.ui.tracker import context_tracker


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")
context_tracker.force_livemux = True


m3u8_url = ""
m3u8_headers = {}
license_key = None


t0 = time.monotonic()
hls_process =  HLS_Downloader(
    m3u8_url=m3u8_url,
    headers=m3u8_headers,
    output_path=fr".\Video\HLS.{conf_extension}",
    key=license_key
)


out_path, need_stop, error = hls_process.start()
print(f"out={out_path} need_stop={need_stop} error={error} elapsed={time.monotonic() - t0:.2f}s")
