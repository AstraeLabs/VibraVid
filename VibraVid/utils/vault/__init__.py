# 29.01.26

from .vault_1 import Vault1Client, claudio_vault
from .vault_2 import lab_vault

__all__ = ["claudio_vault", "lab_vault", "build_named_vault", "all_vaults"]
_BUILTIN_VAULTS = [claudio_vault, lab_vault]


def build_named_vault(name: str, cfg: dict):
    return Vault1Client(base_url=(cfg or {}).get("url", ""), token=(cfg or {}).get("token", ""), name=name)


def all_vaults() -> list:
    return list(_BUILTIN_VAULTS)
