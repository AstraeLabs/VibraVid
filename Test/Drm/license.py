# 12.09.26
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)

from VibraVid.setup.system import _initialize_paths
_initialize_paths()

from VibraVid.core.drm.manager import DRMManager
from VibraVid.setup import get_prd_path, get_wvd_path


LICENSE_URL = "your_license_url_here"
TEST_PSSH = "pssh_data_here"
TEST_KID = "kid_data_here"

pssh_list = [
    {
        "pssh": TEST_PSSH,
        "kid": TEST_KID,
        "label": "widevine_test",
    }
]

drm = DRMManager(
    widevine_device_path=get_wvd_path(),
    playready_device_path=get_prd_path(),
    prefer_remote_cdm=False,
)

keys_manager = drm.get_wv_keys(
    pssh_list=pssh_list,
    license_url=LICENSE_URL,
)
