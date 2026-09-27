# 29.07.25
# ruff: noqa: E402

import os
import sys
import time

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader.ism import ISM_Downloader
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")
context_tracker.force_livemux = True


ism_url = ""
ism_headers = {}
license_url = ""
license_headers = {}
license_key = None


t0 = time.monotonic()
dash_process = ISM_Downloader(
    ism_url=ism_url,
    headers=ism_headers,
    license_url=license_url,
    license_headers=license_headers,
    key=license_key,
    output_path=rf".\Video\ISM.{conf_extension}",
    drm_preference=DRMType.WIDEVINE
)


out_path, need_stop, error = dash_process.start()
print(f"out={out_path} need_stop={need_stop} error={error} elapsed={time.monotonic() - t0:.2f}s")
