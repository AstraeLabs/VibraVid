# 25-06-2020
# ruff: noqa: E402

import os
import sys


# Fix import
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from StreamingCommunity.Util import Logger, start_message
from StreamingCommunity.Lib.MEGA import MEGA_Downloader


start_message()
Logger()
mega = MEGA_Downloader(
    choose_files=True
)

output_path = mega.download_url(
    url="",
    dest_path=r".\Video\Prova.mp4",
)