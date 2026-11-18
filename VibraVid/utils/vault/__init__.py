# 29.01.26

from .default import Vault_di_default, default_vault
from .local import LocalVault, local_vault
from .universal import UniversalVault, build_universal_vault

__all__ = ["default_vault", "local_vault", "build_named_vault", "build_universal_vault", "all_vaults", "UniversalVault", "LocalVault", "DEFAULT_VAULT_KEYS", "LOCAL_VAULT_KEY"]
_BUILTIN_VAULTS = [default_vault]
DEFAULT_VAULT_KEYS = ("default", "vault_1")
LOCAL_VAULT_KEY = "local"


def build_named_vault(name: str, cfg: dict):
    return Vault_di_default(base_url=(cfg or {}).get("url", ""), token=(cfg or {}).get("token", ""), name=name)


def all_vaults() -> list:
    return list(_BUILTIN_VAULTS)
