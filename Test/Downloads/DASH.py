# 29.07.25
# ruff: noqa: E402

import os
import sys


# Fix import
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from StreamingCommunity.Util import Logger, start_message
from StreamingCommunity.Lib.DASH.downloader import DASH_Downloader


start_message()
logger = Logger()


mpd_url = ''
mpd_headers = {}
license_url = ''
license_headers = {}
license_params = {}
license_ley = None

dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    license_url=license_url,
    output_path=r".\Video\Prova.mp4"
)
dash_process.parse_manifest(custom_headers=mpd_headers)

if dash_process.download_and_decrypt(custom_headers=license_headers, query_params=license_params, key=license_ley):
    dash_process.finalize_output()

status = dash_process.get_status()
print(status)