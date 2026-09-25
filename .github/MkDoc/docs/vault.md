# Adding a DRM Key Vault

A vault is an external DRM key store that VibraVid queries before falling back to CDM
extraction, and writes newly-extracted keys back to. Two backends ship today:

| Config key | Class | Protocol | Instance |
|---|---|---|---|
| `vault_1` | `Vault1Client` (`VibraVid/utils/vault/vault_1.py`) | REST |
| `vault_2` | `Vault2Client` (`VibraVid/utils/vault/vault_2.py`) | JSON-RPC |

## No code needed: another REST-compatible endpoint

If the backend speaks the same simple REST contract as `vault_1` — Bearer-token-authenticated
`POST {url}/get-keys` and `POST {url}/save-keys` — just add a named entry under `DRM.vault` in
`Conf/config.json`:

```json
"vault": {
  "vault_1": { "url": "https://drm-db.dev", "token": "" },
  "myvault": { "url": "https://drm.example.com", "token": "my-secret-token" }
}
```

`DRMManager._build_vaults()` (`VibraVid/core/drm/manager.py`) turns any name other than
`vault_1`/`vault_2` into a `Vault1Client` instance via `build_named_vault()`
(`VibraVid/utils/vault/__init__.py`) — no Python code required. See
[Configuration → Multiple / self-hosted DRM vaults](configuration.md#multiple-self-hosted-drm-vaults)
for the full wire contract both endpoints must implement.

## Code needed: a different backend protocol

For a backend that *isn't* REST/JSON-compatible with `vault_1` (a different transport, a
different auth scheme, a different response shape — like `vault_2`'s JSON-RPC), subclass
`BaseVault` (`VibraVid/utils/vault/base.py`).

### `BaseVault`

`BaseVault` gives every vault a shared `__init__(name)`, `close()`, `_clean_license_url()` and
`_prewarm()`, plus a safe no-op default for every method a caller might invoke generically
across all vaults — override only what your backend actually supports:

```python
class BaseVault:
    def __init__(self, *, name: str): ...
    @property
    def is_connected(self) -> bool: ...                                             # default: False
    def close(self) -> None: ...                                                    # default: closes self.session if set
    def track_download_async(self, title, media_type, service=None) -> None: ...    # default: no-op
    def set_keys(self, keys_list, license_url, pssh=None, kid_to_label=None) -> int: ...      # default: 0
    def get_keys_by_pssh(self, license_url, pssh) -> list[str]: ...                 # default: []
    def get_keys_by_kids(self, license_url, kids, pssh=None) -> list[str]: ...      # default: []
    def get_keys_by_kid(self, license_url, kid) -> list[str]: ...                   # default: delegates to get_keys_by_kids
    def report_wrong_key(self, kid, key, license_url, pssh=None) -> bool: ...       # default: False
```

`get_keys_by_kids()` is the one method every vault should realistically implement — it's the
only lookup path `DRMManager` actually drives (`_db_lookup()` in `manager.py`). Everything else
is optional: `vault_2.py` (`Vault2Client`) only overrides `is_connected` and `get_keys_by_kids`
and inherits every other default as-is.

### Minimal example

```python
# VibraVid/utils/vault/vault_3.py
from VibraVid.utils.vault.base import BaseVault
from VibraVid.utils.config import config_manager

db_config = config_manager.config.get_dict("DRM", "vault")
VAULT_URL = db_config.get("vault_3", {}).get("url", "")

class Vault3Client(BaseVault):
    def __init__(self, *, name: str):
        super().__init__(name=name)
        # set up self.session / auth here

    @property
    def is_connected(self) -> bool:
        return bool(VAULT_URL)

    def get_keys_by_kids(self, license_url, kids, pssh=None) -> list[str]:
        # query your backend, return ["kid:key", ...]
        ...

my_vault = Vault3Client(name="myvault")
```

### Registration

A new backend singleton is registered in exactly **one place**, `_BUILTIN_VAULTS` in
`VibraVid/utils/vault/__init__.py`:

```python
# VibraVid/utils/vault/__init__.py
from .vault_3 import my_vault

_BUILTIN_VAULTS = [claudio_vault, lab_vault, my_vault]
```

This list backs `all_vaults()`, which generic callers use instead of importing and naming each
singleton by hand — e.g. `MediaDownloader._report_wrong_key_to_vaults()`
(`VibraVid/core/velora/downloader.py`) does `for vault in all_vaults(): ...` to notify every
built-in vault of a confirmed-wrong key, with no per-vault code to update when a new one is
added here.

`DRMManager._build_vaults()` (`VibraVid/core/drm/manager.py`) is a **separate**, ordered list —
it decides lookup *priority* (which vault is queried first) and additionally still needs an
explicit reserved-name branch for a config-key like `vault_2`/`vault_3` that isn't REST/
`build_named_vault()`-compatible, the same way `vault_2` is special-cased today:

```python
# VibraVid/core/drm/manager.py
if name == "vault_2":
    continue  # reserved: always the JSON-RPC singleton, not a REST config entry
```

Add a matching `if name == "vault_3": continue` guard plus an `if my_vault.is_connected: ...`-style
unconditional append for your singleton at the top of `_build_vaults()` if it should also
participate in key lookups, not just wrong-key reporting.

### Reporting a wrong key

`report_wrong_key(kid, key, license_url, pssh)` is called in the background (fire-and-forget,
from `MediaDownloader._report_wrong_key_to_vaults()` in `VibraVid/core/velora/downloader.py`)
whenever a post-decrypt key-sanity check proves a stored key wrong. Implement it only if your
backend has a way to record that (e.g. `vault_1`'s `save-keys` with `invalid: true`,
incrementing a `warning_count`); otherwise leave `BaseVault`'s no-op default, as `vault_2` does
— its JSON-RPC backend has no equivalent concept.
