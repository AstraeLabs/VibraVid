# 21.05.24

import json
from pathlib import Path

from VibraVid.core.ui.tracker import context_tracker

DEFAULT_REGION = "it"
_REGIONS_FILE = Path(__file__).with_name("regions.json")
REGIONS: dict = json.loads(_REGIONS_FILE.read_text(encoding="utf-8"))


def get_region() -> str:
    """Return the active region code ('it' or 'es'), defaulting to it."""
    try:
        raw = (context_tracker.site_options or {}).get("country")
    except Exception:
        raw = None
    code = (raw or DEFAULT_REGION).strip().lower()
    return code if code in REGIONS else DEFAULT_REGION


def region_conf() -> dict:
    """Return the config dict for the active region."""
    return REGIONS[get_region()]
