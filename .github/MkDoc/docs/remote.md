# Remote server (resolve on a server, download on a client)

One machine runs the **services** (login, search, title/track/manifest resolution); any number of clients run the **download**.

| Where | What runs |
|---|---|
| Server | service code: login, search, seasons/episodes, manifest and playback resolution, the service's own license call |
| Client | track selection (`-sv/-sa/-ss`), download, decrypt, mux, naming/output folders, vault lookup, your own CDM |

Licenses, per API key:

* **Client CDM (default)**: your CDM builds the challenge; the server relays it to the service (using the service's signed call if it has one). Lookup order: manifest -> KID -> vault -> CDM only if the key isn't in the vault.
* **Server CDM** (`server_cdm`): the server runs the whole key flow (vault, then its CDM) and returns `kid:key` pairs; the client needs no CDM. Granted per key.

## Server

`Conf/remote.json`:

```json
{
  "serve": {
    "host": "127.0.0.1",
    "port": 8786,
    "sites": ["appletv", "disney"],
    "users": {
      "a-long-random-secret": { "username": "alice", "sites": ["appletv"], "server_cdm": true },
      "another-secret":       { "username": "bob" }
    }
  }
}
```

| Key | Description |
|-----|-------------|
| `users` | API key -> `username`, optional `sites` allowlist, optional `server_cdm` (default `false`) |
| `sites` | Optional global allowlist; a key gets the **intersection** with its own list. Omit a list for "everything" |
| `host` | Default `127.0.0.1`; change only to really expose the server |
| `session_ttl` | Idle seconds (default 300) |
| `max_sessions` | Default 100 |
| `bind_session_to_ip` | Default `true` |

```bash
python GUI/manage.py remote_serve        # --host / --port override the config
```

It refuses to start without a key; `--no-key` serves without keys, loopback only.

The server never uses an account of its own: each request runs with **the client's** login (see [Logging in through the server](#logging-in-through-the-server-login-logout)). A service with no client login runs anonymous.

## Client

Add the server to **your** `Conf/remote.json`:

```json
{
  "remote_services": {
    "home": { "url": "http://203.0.113.10:8786", "api_key": "a-long-random-secret", "server_cdm": false }
  }
}
```

```bash
python manual.py --server home -i appletv -s "Some Title" -S 1 -E "*"
python manual.py --server -i appletv -s "Some Film" --item 0          # only one server configured
python manual.py --server home -i appletv -s "Some Show" -S 1 -E 3 --server-opt drm=playready --server-opt atmos=true
```

* `--server [NAME]`: `NAME` can be omitted when exactly one server is configured.
* `-i` takes the **site name** (the index works only if the service is installed locally).
* `-s`, `--item` (0-based), `-S`, `-E` work as in a normal run; otherwise you are prompted (row number, `q` to quit).
* Service options are flags (`--url`, `--store`, ...) or `--server-opt KEY=VALUE` (repeatable); options set for a locally installed service are forwarded too.
* `server_cdm: true` applies only if the API key has the grant; otherwise your local CDM is used with a warning.

### Writing a service for a remote server

A service works unchanged if it reads its login when needed (`config_manager.login.get(...)`) and keeps its logged-in client in a module global named like `_client`, `_xxx_client`, `_client_by_region`...

* **Don't read the login at import time** (`_token = config_manager.login.get(...)` at module level): it would be read once, for nobody. Read it inside the login function. (`disney` still does this.)
* To name identity-bound globals yourself, add `_identity_state = ("_client", "_session")` to the service module; it replaces the naming guess.

## Logging in through the server (`--login`, `--logout`)

A service's own login runs on the server while the account stays with you:

```bash
python manual.py --server home -i amazon_music --login tv
python manual.py --server home -i amazon_music --logout
```

* Output (pairing link and code, progress) appears in your terminal; links are not opened on the server.
* Its questions (region, "press Enter after authorizing"...) are asked in your terminal.
* Tokens are stored in **your** `Conf/login.toml` / `.cache`; the server writes nothing and forgets the login at the end (Ctrl+C cancels; an unanswered login ends after 5 minutes).
* The service doesn't need to be installed on the client.

Outside a login, a service that stops to ask something fails at once (`asked for input a server cannot give`). Options like `--csv` (reads a local file) must run locally.

## Service options (`--url` and the rest)

The server lists each service's own options (`--url`, `--store`, `--atmos`, `--drm`...) at `GET /api/remote/sites/<site>/info/`, so they work without the service installed locally:

```bash
python manual.py --server home -i appletv --url "https://tv.apple.com/it/movie/..." --store US
python manual.py --server home -i appletv -s "Some Title" --atmos
```

`--url` replaces `-s` (the server resolves the link without searching). The same values go to search, seasons and download. `--server-opt KEY=VALUE` sets any option by hand.

## Exposing the server

The server has no TLS. Pick one:

* a VPN (Tailscale, WireGuard), binding to the VPN address: simplest and safest;
* a reverse proxy with HTTPS (Caddy, nginx) or a tunnel in front of `127.0.0.1:8786`;
* a LAN address with `--host`.

Sessions are bound to the client IP (`REMOTE_ADDR`); behind a reverse proxy that is the proxy's address, shared by everyone. Set `bind_session_to_ip` to `false` if that gets in the way (the API key is still required).

Anyone with a key can use the server's accounts: one key per person, plus the `sites` allowlist. Keys are compared in constant time and never logged (only the username).
