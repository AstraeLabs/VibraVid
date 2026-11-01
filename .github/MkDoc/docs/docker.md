# Docker

## Recommended: Docker Compose

```bash
docker-compose up -d        # Start
docker-compose logs -f      # View logs
docker-compose down         # Stop (data persists)
```

For NAS users (Synology, TrueNAS, Unraid, etc.), see the [NAS deployment guide](nas.md) for a step-by-step setup guide including bind mounts and permission configuration.

## Migrating existing JSON configuration

After upgrading from JSON configuration, migrate the files in the mounted `/app/Conf` directory locally. If the upgrade already created TOML defaults, use `--overwrite` to back them up and replace them with your migrated JSON settings:

```bash
docker compose exec --user appuser vibravid python manual.py --migrate-config /app/Conf --dry-run --overwrite
docker compose exec --user appuser vibravid python manual.py --migrate-config /app/Conf --overwrite
docker compose restart vibravid
```

Run the commands while downloads are idle. Review the dry-run report and any edits made only in TOML before proceeding. The command uses `/app/Conf.defaults` from the image, keeps the JSON originals, and backs up sources and replaced files in a private `migration-*` directory on the same persistent volume. No credentials leave the container. See [Migrating from JSON](configuration.md#migrating-from-json) for mappings and recovery details.

## Custom paths and ports

Copy the provided template and edit the values you need:

```bash
cp .env.example .env
```

Key variables (full list in `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `VIBRAVID_PORT` | `8000` | Host port exposed by the container |
| `VIBRAVID_BIND` | `0.0.0.0` | Interface the port is published on. `127.0.0.1` = this machine only |
| `VIBRAVID_USERNAME` / `VIBRAVID_PASSWORD` | unset | Set both to require a login on every GUI page (see [Securing the GUI](#securing-the-gui)) |
| `VIBRAVID_ALLOW_SERVICE_UPLOAD` | `0` | `1` allows provider ZIP upload, git `imp_service` refresh and editing `[HOOKS]`/`imp_service` from the web UI |
| `VIBRAVID_BOT_SECRET` | unset | Token for the Telegram bot (`X-VibraVid-Token`); required for `/coda` when the login is on |
| `VIBRAVID_VIDEO_DIR` | named volume | Where downloads land on the host (e.g. `/volume2/Movies`) |
| `VIBRAVID_DB_DIR` | named volume | Host path for the SQLite database |
| `VIBRAVID_CONFIG_DIR` | named volume | Host path for `config.toml` / `login.toml` |
| `VIBRAVID_LOGS_DIR` | named volume | Host path for application logs |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames Django accepts |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8000,...` | Origins for CSRF validation |
| `PUID` / `PGID` | unset | User/group ID the container process runs as — set these to your host user's `id -u`/`id -g` so downloaded files aren't owned by root (see the [NAS guide](nas.md) for the full walkthrough) |
| `VIBRAVID_HIDDEN_SITES` | unset | Comma-separated service folder names to switch off (CLI, TUI and GUI), added to `hidden_services` in `config.toml` (see [Configuration](configuration.md#default)). Uncomment it in `docker-compose.yml` |
| `WATCHLIST_AUTO_INTERVAL_SECONDS` | `14400` (4h) | Initial interval (min 900) of the watchlist auto-download loop. An interval saved from the GUI's Watchlist page takes precedence after that |

## Optional sidecar (Telegram bot)

The [Telegram bot](telegram.md) is an opt-in Compose service behind the `telegram`
profile (`docker-compose --profile telegram up -d`) — see that guide for its own environment
variables.

**NAS example** — store downloads on a NAS share, expose on port 9000:

```env
VIBRAVID_PORT=9000
VIBRAVID_VIDEO_DIR=/volume2/Movies
VIBRAVID_DB_DIR=/volume1/docker/vibravid/db
VIBRAVID_CONFIG_DIR=/volume1/docker/vibravid/conf
VIBRAVID_LOGS_DIR=/volume1/docker/vibravid/logs
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.100
CSRF_TRUSTED_ORIGINS=http://192.168.1.100:9000
```

Then start normally:
```bash
docker-compose up -d
```

## Private Network Deployment

Uncomment and edit the `environment` section in `docker-compose.yml`:

```yaml
environment:
  DJANGO_DEBUG: "false"
  ALLOWED_HOSTS: "streaming.example.local,localhost,127.0.0.1,192.168.1.50"
  CSRF_TRUSTED_ORIGINS: "https://streaming.example.local"
  USE_X_FORWARDED_HOST: "true"
  SECURE_PROXY_SSL_HEADER_ENABLED: "true"
  CSRF_COOKIE_SECURE: "true"
  SESSION_COOKIE_SECURE: "true"
  DJANGO_SECRET_KEY: "your-secure-secret-key-here"
```

ARR-related variables can be added to the same `environment` block:

```yaml
environment:
  USE_ARR_SERVICES: "true"
  SONARR_URL: "http://sonarr:8989"
  SONARR_API_KEY: "your-sonarr-api-key"
  RADARR_URL: "http://radarr:7878"
  RADARR_API_KEY: "your-radarr-api-key"
  SEERR_WEBHOOK_SECRET: "your-seerr-secret"
  SONARR_WEBHOOK_SECRET: "your-sonarr-secret"
  RADARR_WEBHOOK_SECRET: "your-radarr-secret"
```

## Manual Docker Build

```bash
docker build -t vibravid .

docker run -d \
  --name vibravid \
  -p 8000:8000 \
  -v vibravid_db:/app/data \
  -v vibravid_videos:/app/Video \
  -v vibravid_logs:/app/logs \
  -v vibravid_config:/app/Conf \
  --restart unless-stopped \
  vibravid
```

## Binding Local Folders

```bash
# Linux/macOS
docker run -d --name vibravid -p 8000:8000 \
  -v ~/Downloads/Videos:/app/Video \
  vibravid

# Windows (PowerShell)
docker run -d --name vibravid -p 8000:8000 `
  -v "D:\Video:/app/Video" `
  vibravid
```

## Securing the GUI

By default the GUI has no login and listens on every interface, so anyone who can reach port 8000 controls the app (settings, logs, downloads). Before exposing it beyond a trusted network:

1. **Require a login**: set `VIBRAVID_USERNAME` and `VIBRAVID_PASSWORD` in `.env`. Webhooks (`/api/arr/webhook/*`, with their secrets), the Telegram bot (`VIBRAVID_BOT_SECRET`) and the remote API (`X-VibraVid-Key`) keep their own tokens; set those secrets too.
2. **Limit the interface**: set `VIBRAVID_BIND=127.0.0.1` and publish it through a reverse proxy with TLS (then also set `USE_X_FORWARDED_HOST`, `CSRF_TRUSTED_ORIGINS` and `SESSION_COOKIE_SECURE`).
3. **Leave provider upload disabled**: uploading a service ZIP, refreshing `imp_service` git sources and editing `[HOOKS]`/`imp_service` run code on the server, so they need `VIBRAVID_ALLOW_SERVICE_UPLOAD=1`.
4. **Keep `docker.sock` unmounted** unless you want the one-click updater.

## Updating (Docker)

When a new version is released, VibraVid shows an **update banner** in the web UI. Click **Update now** to update in place.

**One-click update** requires the Docker socket to be mounted (it is commented out in the default `docker-compose.yml`; uncomment it to opt in):

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

With the socket mounted, **Update now** drives the host Docker daemon directly: it launches a short-lived helper container that runs `docker compose pull && docker compose up -d`, pulling the published image (`ghcr.io/astraelabs/vibravid:latest`) and recreating the container. No host-side script is required.

!!! warning "Security note"
    Mounting the Docker socket grants the container control of the host Docker daemon (root on
    the host). Only enable it together with a GUI login. Without the socket the button tells
    you to update manually.

**Manual update** (always works, socket or not):

```bash
docker compose pull
docker compose up -d
```
