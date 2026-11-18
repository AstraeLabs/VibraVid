# 29.07.25
# ruff: noqa: E402


import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.core.drm.manager import DRMManager

drm = DRMManager()

results = drm.add_keys(
    keys=[],
    license_url="",
    pssh="",
    kid_to_label={},
)

print(results)
