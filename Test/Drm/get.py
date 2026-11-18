# 07.08.26
# ruff: noqa: E402


import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.core.drm.manager import DRMManager

drm = DRMManager()

kids = []

print(f"Connected vaults: {[vdb.name for vdb in drm._vaults]}")

for kid in kids:
    print(f"\n--- KID {kid} ---")
    for vdb in drm._vaults:
        try:
            keys = vdb.get_keys_by_kids(None, [kid])
        except Exception as e:
            print(f"{vdb.name}: ERROR {e}")
            continue

        print(f"{vdb.name}: FOUND {keys}" if keys else f"{vdb.name}: not found")
