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


m3u8_url = "https://vod-dsc-eu-south-1-mrs1-dss.media.dssott.com/dvt3=exp=1790435996~url=%2Fps01%2Fdisney%2Fdedc42a9-96f6-41a6-b87c-8e5932579d4f%2F~aid=89a4cd19-8d0f-4ead-bcab-ca6623b63bfc~did=8b22352c-2823-4131-bbe1-f5fd0e69af97~country=IT~kid=k02~hmac=6d70b093aa0640bc4b137b1781879919307cf1d7606df77e53fd335b4796c396/ps01/disney/dedc42a9-96f6-41a6-b87c-8e5932579d4f/una-ctr-all-30164a8a-a80d-4726-9a1e-96c6df05390c-ec68fc20-7ad1-4873-b8c1-c5d28dda01e5.m3u8?a=3&r=1080&rmin=720&v=1&hash=7bd9329c87221eb1dab6571b2bc31422267e1148"
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
