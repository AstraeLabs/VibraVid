# Adding a DRM Key Vault

A vault is an external DRM key store that VibraVid queries before falling back to CDM extraction.
Most vaults need only a config entry under `DRM.vault`. See
[Configuration → Multiple / self-hosted DRM vaults](configuration.md#multiple-self-hosted-drm-vaults)
for the fields.

```toml
[DRM.vault.default]
url = "https://drm-db.dev"
token = ""

[DRM.vault.myvault]
url = "https://drm.example.com"
token = "..."
protocol = "custom"

[DRM.vault.public]
url = "https://vault.example.org"
token = "..."
```

`protocol` is optional: without it VibraVid detects it once and caches it in
`.cache/services/vault/universal_<name>.json`.

## A new protocol

Only a backend that speaks none of `custom` / `api` / `json` / `query` needs code. Subclass `BaseVault`
(`VibraVid/utils/vault/base.py`) and override what the backend supports:

```python
from VibraVid.utils.vault.base import BaseVault

class MyVault(BaseVault):
    @property
    def is_connected(self) -> bool:
        return True

    def get_keys_by_kids(self, license_url, kids, pssh=None) -> list[str]:
        return []  # ["kid:key", ...]
```

- `get_keys_by_kids()` is the lookup `DRMManager` uses.
- `is_writable` + `set_keys()`: only if the vault should receive newly extracted keys (default: read-only).
- `report_wrong_key()`: only if the backend can record a key proven wrong by flux's key-sanity check.
- `replace_key()`: swaps a reported key for the corrected one (the default just calls `set_keys()`).

Then return your instance for its config key in `DRMManager._build_vaults()` (`VibraVid/core/drm/manager.py`).
