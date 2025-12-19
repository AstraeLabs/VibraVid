# 23.06.24
# ruff: noqa: E402

import os
import sys


# Fix import
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from StreamingCommunity.Util import Logger, start_message
from StreamingCommunity.Lib.HLS import HLS_Downloader


start_message()
Logger()
hls_process =  HLS_Downloader(
    m3u8_url="",
    headers={},
    license_url=None,
    output_path=r".\Video\Prova.",
).start()

thereIsError = hls_process['error'] is not None
print(thereIsError)