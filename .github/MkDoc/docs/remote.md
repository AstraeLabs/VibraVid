# Remote server (resolve on a server, download on a client)

One machine runs the **services** (login, search, title/track/manifest resolution); any number of
clients run the **download** (track selection, segment download, decrypt, mux).

| Where | What runs |
|---|---|
| Server | the service code and its login, search, seasons/episodes, manifest and playback resolution; the service's own license call |
| Client | track selection (`-sv/-sa/-ss`), download, decrypt, mux, naming and output folders, vault lookup, your own CDM |

Licenses have two modes, per API key:

* **Client CDM (default)** — your CDM builds the license challenge; the server only relays it to the
  service (using the service's own signed call when it has one). Key lookup order is unchanged:
  manifest → KID → vault → CDM only when the key is not in the vault.
* **Server CDM** (`server_cdm`) — the server runs the whole key flow (vault, then its CDM) and returns
  `kid:key` pairs; the client needs no CDM. Granted per key.

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

* `users` — API key → `username`, optional `sites` allowlist, optional `server_cdm` (default `false`).
* `sites` — optional global allowlist. A key can use the **intersection** of this and its own list.
  Leave a list out for "everything".
* `host` defaults to `127.0.0.1`. Change it only when you really expose the server (see below).
* Optional: `session_ttl` (idle seconds, default 300), `max_sessions` (100), `bind_session_to_ip` (true).

Run it:

```bash
python GUI/manage.py remote_serve
```

It refuses to start without at least one key. `--no-key` serves without keys, but only on a loopback
address. Use `--host` / `--port` to override the config.

The server never uses an account of its own for a client: each request runs with **the client's** login
(see [Credentials](#credentials)). A service with no login from the client runs anonymous.

## Client

Add the server to **your** `Conf/remote.json`:

```json
{
  "remote_services": {
    "home": { "url": "http://203.0.113.10:8786", "api_key": "a-long-random-secret", "server_cdm": false }
  }
}
```

Then:

```bash
python manual.py --server home -i appletv -s "Some Title" -S 1 -E "*"
python manual.py --server -i appletv -s "Some Film" --item 0          # only one server configured
python manual.py --server home -i appletv -s "Some Show" -S 1 -E 3 --server-opt drm=playready --server-opt atmos=true
```

* `--server [NAME]` — `NAME` can be omitted when exactly one server is configured.
* `-i` takes the **site name** (the index works only if the service is installed locally).
* `-s`, `--item` (0-based), `-S`, `-E` behave like a normal run; without them you are asked. Results are shown in the
  usual results table: type the row number, `q` to quit.
* The service's own options are available as flags (`--url`, `--store`, ...) and as `--server-opt KEY=VALUE`
  (repeatable). Options you set for a locally installed service are forwarded too.
* `server_cdm: true` is only used when the API key also has the grant; otherwise your local CDM is used
  and a warning is printed.

### Writing a service for a remote server

A service works unchanged if it reads its login when it needs it (`config_manager.login.get(...)`) and keeps its
logged-in client in a module global named like `_client`, `_xxx_client`, `_client_by_region`... Two things need care:

* **Do not read the login at import time** (`_token = config_manager.login.get(...)` at module level): it is read
  once, for nobody. Read it inside the function that logs in. (`disney` still does this.)
* To name the identity-bound globals yourself, add `_identity_state = ("_client", "_session")` to the service module;
  it replaces the naming guess.

## Logging in through the server (`--login`, `--logout`)

The service code lives on the server, so a service's own login runs there too, while the account stays with you:

```bash
python manual.py --server home -i amazon_music --login tv
python manual.py --server home -i amazon_music --logout
```

* what the service prints (the pairing link and code, progress) appears in your terminal, links are not opened on the
  server's screen;
* its questions (region, "press Enter after authorizing"...) are asked in your terminal and your answers go back;
* the tokens it obtains are stored in **your** `Conf/login.toml` / `.cache`, exactly as in a local run; the server writes
  nothing and forgets the login when it ends (Ctrl+C cancels it, an unanswered login ends after 5 minutes);
* the service does not need to be installed on the client.

Outside a login, a service that stops to ask something still fails at once (`asked for input a server cannot give`).
Other options (`--csv` reads a file of yours) must run locally.

## Service options (`--url` and the rest)

Services have options of their own (`--url`, `--store`, `--atmos`, `--drm`...). The server lists them
(`GET /api/remote/sites/<site>/info/`), so they work on a client that does not have the service installed:

```bash
python manual.py --server home -i appletv --url "https://tv.apple.com/it/movie/..." --store US
python manual.py --server home -i appletv -s "Some Title" --atmos
```

`--url` replaces `-s`: the server resolves the link without searching. The same values go to the search, the
seasons and the download steps. `--server-opt KEY=VALUE` sets any option by hand.

## Exposing the server

There is no TLS in the server itself. Pick one:

* a VPN (Tailscale, WireGuard) and bind to the VPN address — simplest and safest;
* a reverse proxy with HTTPS (Caddy, nginx) or a tunnel in front of `127.0.0.1:8786`;
* a LAN address with `--host`.

Sessions are bound to the client's IP (`REMOTE_ADDR`); behind a reverse proxy that is the proxy's address,
so everyone shares it. Set `bind_session_to_ip` to `false` if that gets in the way (the API key is still
required).

Anyone with a key can use the server's accounts, so give out one key per person and use the `sites`
allowlist. Keys are compared in constant time and never written to the logs (only the username is).