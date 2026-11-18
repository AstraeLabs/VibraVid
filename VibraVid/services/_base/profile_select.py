# 25.10.26

import logging
from dataclasses import dataclass

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.utils import config_manager
from VibraVid.utils.config import LoginConfigError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountProfile:
    id: str
    name: str


class ProfileSelector:
    def __init__(self, site: str):
        self.site = site

    @property
    def wanted(self) -> str:
        """The requested profile (``--user-profile`` wins over login.toml); empty keeps the profile the credentials already point to."""
        opts = getattr(context_tracker, "site_options", None) or {}
        value = opts.get("user_profile")
        if value is None or str(value).strip() == "":
            try:
                value = config_manager.login.get(self.site, "profile", default=None)
            except Exception:
                value = None
        return "" if value is None else str(value).strip()

    def pick(self, profiles: list[AccountProfile]) -> AccountProfile | None:
        """The requested profile among *profiles*; ``None`` when none is requested, ``LoginConfigError`` (listing the choices) when it does not exist."""
        wanted = self.wanted
        if not wanted:
            return None

        if wanted.isdigit() and 1 <= int(wanted) <= len(profiles):
            return profiles[int(wanted) - 1]

        for profile in profiles:
            if profile.name.strip().casefold() == wanted.casefold():
                return profile

        raise LoginConfigError(f'{self.site}: profile "{wanted}" not found, available profiles: {self.listing(profiles)}')

    @staticmethod
    def listing(profiles: list[AccountProfile], chosen: AccountProfile | None = None) -> str:
        """``1 Name, 2 Name (selected)``: the numbers to write in login.toml."""
        return ", ".join(f"{i} {p.name}{' (selected)' if p == chosen else ''}" for i, p in enumerate(profiles, 1))

    def select(self, client) -> AccountProfile | None:
        """Switch *client* to the requested profile, if any; returns it (``None`` when no profile is requested)."""
        if not self.wanted:
            return None

        profiles = client.list_profiles()
        if not profiles:
            raise RuntimeError(f"{self.site}: could not read the account profiles")

        chosen = self.pick(profiles)
        logger.info(f"{self.site} profiles: {self.listing(profiles, chosen)}")
        client.switch_profile(chosen)
        return chosen
