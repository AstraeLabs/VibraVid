"""Validate the Telegram bot environment template without Docker."""

import re
from collections import Counter
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[2] / ".env.telegram.example"
REQUIRED = {"TG_BOT_TOKEN", "TG_API_ID", "TG_API_HASH", "TG_ALLOWED_USERS"}


def test_telegram_env_example_has_unique_required_keys():
    content = TEMPLATE.read_text(encoding="utf-8")
    keys = re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\s*=", content)
    duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)

    assert REQUIRED.issubset(keys), f"Missing Telegram configuration keys: {sorted(REQUIRED - set(keys))}"
    assert not duplicates, f"Duplicate Telegram environment keys: {duplicates}"
