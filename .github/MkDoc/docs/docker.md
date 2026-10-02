# Docker

## Recommended: Docker Compose

```bash
docker-compose up -d        # Start
docker-compose logs -f      # View logs
docker-compose down         # Stop (data persists)
```

For NAS users (Synology, TrueNAS, Unraid, etc.), see the [NAS deployment guide](nas.md) for a step-by-step setup guide including bind mounts and permission configuration.

## Custom paths and ports

Copy the provided template and edit the values you need:

```bash
cp .env.example .env
```

Key variables (full list in `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `VIBRAVID_PORT` | `8000` | Host port exposed by the container |
| `VIBRAVID_VIDEO_DIR` | named volume | Where downloads land on the host (e.g. `/volume2/Movies`) |
| `VIBRAVID_DB_DIR` | named volume | Host path for the SQLite database |
| `VIBRAVID_CONFIG_DIR` | named volume | Host path for `config.json` / `login.json` |
| `VIBRAVID_LOGS_DIR` | named volume | Host path for application logs |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames Django accepts |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8000,...` | Origins for CSRF validation |
| `PUID` / `PGID` | unset | User/group ID the container process runs as — set these to your host user's `id -u`/`id -g` so downloaded files aren't owned by root (see the [NAS guide](nas.md) for the full walkthrough) |
| `WATCHLIST_AUTO_INTERVAL_SECONDS` | `14400` (4h) | Initial interval (min 900) of the watchlist auto-download loop. An interval saved from the GUI's Watchlist page takes precedence after that |

## Optional sidecar (Bypasser)

Monochrome (Amazon Music) needs the **Bypasser** sidecar reachable to solve its Cloudflare
Turnstile challenge — there's no in-process fallback. It ships as a separate Compose service
gated behind a **profile**, so it's not started by default:

```bash
docker-compose --profile bypasser up -d
```

The app container talks to it via `BYPASSER_URL` (already wired to the sidecar hostname in the
shipped `docker-compose.yml`). Its live status is shown on the GUI's Settings page.

The [Telegram bot](telegram.md) is likewise an opt-in Compose service behind the `telegram`
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

## Updating (Docker)

When a new version is released, VibraVid shows an **update banner** in the web UI. Click **Update now** to update in place.

**One-click update** requires the Docker socket to be mounted (it is in the default `docker-compose.yml`):

```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
```

With the socket mounted, **Update now** drives the host Docker daemon directly: it launches a short-lived helper container that runs `docker compose pull && docker compose up -d`, pulling the published image (`ghcr.io/astraelabs/vibravid:latest`) and recreating the container. No host-side script is required.

!!! warning "Security note"
    Mounting the Docker socket grants the container control of the host Docker daemon. If
    you'd rather not, comment out the socket volume — the button will then tell you to
    update manually.

**Manual update** (always works, socket or not):

```bash
docker compose pull
docker compose up -d
```
