# VibraVid — NAS Deployment Guide

This guide explains how to run VibraVid on a NAS or home server using Docker Compose. Dedicated sections cover **Synology Container Manager** (DSM 7.2+), **QNAP Container Station** (ARM64), and generic **Linux hosts** (Ubuntu, Debian, Raspberry Pi OS, etc.).

---

## Prerequisites

- Docker and Docker Compose installed on the NAS
- The VibraVid repository cloned or downloaded

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

---

## Generic Linux host (Ubuntu, Debian, Raspberry Pi OS, and any distribution)

This section applies to any Linux host running Docker: a mini-PC, a Raspberry Pi, a home server, or a machine with an external USB/SATA drive. The steps are the same regardless of distribution.

### 1. Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` with the paths and settings that match your setup. At minimum, set the download directory and your host IP. For an external drive mounted at `/mnt/external`:

```env
# Path on the host where downloads will be stored (e.g. an external drive)
VIBRAVID_VIDEO_DIR=/mnt/external/vibravid

# Optional: database and config on the host (recommended for easy backups)
VIBRAVID_DB_DIR=/mnt/data/appdata/vibravid/db
VIBRAVID_CONFIG_DIR=/mnt/data/appdata/vibravid/conf
VIBRAVID_LOGS_DIR=/mnt/data/appdata/vibravid/logs

# Expose on port 8000 (or change to avoid conflicts)
VIBRAVID_PORT=8000

# Allow access from other machines on the LAN
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.100
CSRF_TRUSTED_ORIGINS=http://192.168.1.100:8000
```

### 2. Set PUID / PGID (recommended)

To avoid permission issues on the host-mounted folders, set the user and group IDs that own those folders. Run the following on the NAS host to find your user's IDs:

```bash
id <your-username>
# Example output: uid=1000(john) gid=1000(john) groups=...
```

Add to `.env`:

```env
PUID=1000
PGID=1000
```

The container entrypoint will remap `appuser` to these IDs at startup so all downloaded files are owned by your normal user.

### 3. Start the container

```bash
docker compose up -d
```

If you plan to use `monochrome` (Amazon Music), start the optional Bypasser sidecar alongside
the main app instead — it's off by default:

```bash
docker compose --profile bypasser up -d
```

See the [Docker guide](docker.md#optional-sidecar-bypasser) for details.

On first start, the container automatically:
- Seeds `/app/Conf` from the image defaults if the volume is empty
- Runs Django database migrations

Check logs to confirm everything started correctly:

```bash
docker compose logs -f
```

### 4. Access VibraVid

Open a browser and navigate to:

```
http://<nas-ip>:8000
```

---

## Synology Container Manager (DSM 7.2+)

### Step 1 — Open Container Manager

In DSM, go to **Main Menu → Container Manager**.

### Step 2 — Create a new Project

1. Click **Project → Create**.
2. Give the project a name (e.g., `vibravid`).
3. Choose **Set a path** for the project source and select the folder where you cloned the repository (e.g., `/volume1/docker/vibravid`).
4. Container Manager will detect `docker-compose.yml` automatically.

### Step 3 — Configure environment variables

Before building, click **Next** to reach the environment variable screen. Add the variables from your `.env` file here, or set them directly in the UI:

| Variable | Example value |
|---|---|
| `VIBRAVID_VIDEO_DIR` | `/volume2/Media/Movies` |
| `VIBRAVID_DB_DIR` | `/volume1/docker/vibravid/db` |
| `VIBRAVID_CONFIG_DIR` | `/volume1/docker/vibravid/conf` |
| `VIBRAVID_LOGS_DIR` | `/volume1/docker/vibravid/logs` |
| `PUID` | `1026` (your DSM user ID) |
| `PGID` | `100` (your DSM group ID) |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,192.168.1.x` |
| `CSRF_TRUSTED_ORIGINS` | `http://192.168.1.x:8000` |

To find your Synology user's UID/GID, SSH into the NAS and run:

```bash
id <your-dsm-username>
```

### Step 4 — Port mapping

Container Manager shows the port mapping from `docker-compose.yml`. The default is `8000 → 8000`. Change the left side (host port) if that port is already in use.

### Step 5 — Build and run

Click **Done** to build the image and start the container. The first build downloads all dependencies and may take a few minutes.

### Step 6 — Access VibraVid

Open `http://<nas-ip>:8000` in a browser on your local network.

---

## Bind mounts on Synology

Bind mounts let you store data in regular Synology shared folders (visible in File Station) instead of Docker-managed volumes.

### Folder structure example

```
/volume1/docker/vibravid/
    conf/          ← VIBRAVID_CONFIG_DIR
    db/            ← VIBRAVID_DB_DIR
    logs/          ← VIBRAVID_LOGS_DIR

/volume2/Media/
    Movies/        ← VIBRAVID_VIDEO_DIR (bulk storage)
```

Create the directories via SSH or File Station before starting the container:

```bash
mkdir -p /volume1/docker/vibravid/{conf,db,logs}
mkdir -p /volume2/Media/Movies
```

Then set the corresponding variables in `.env` or in the Container Manager UI.

### Permissions

If downloads land with root ownership, ensure `PUID` and `PGID` are set correctly (see Step 3 above). You can verify after the container starts:

```bash
ls -ln /volume2/Media/Movies
# Files should show your UID:GID, not 0:0
```

---

## QNAP NAS (ARM64)

QNAP devices frequently use ARM64 processors. The VibraVid Docker image is published as a **multi-arch manifest** (`linux/amd64` + `linux/arm64`), so you can run it on QNAP without building from source.

> **Do not run `docker compose up --build` on ARM64.** The source repository includes x86_64-only prebuilt binaries that cause the build to fail on ARM64. Always use `docker compose pull` to fetch the pre-built image.

### Setup via QNAP Container Station

1. Install **Container Station** from the QNAP App Center if not already present.
2. Open a terminal via SSH or the QNAP shell and clone the repository:

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

3. Create your `.env` file:

```bash
cp .env.example .env
```

Edit `.env` for your QNAP paths. QNAP shared folders are typically under `/share/`:

```env
VIBRAVID_VIDEO_DIR=/share/Multimedia/vibravid
VIBRAVID_DB_DIR=/share/Container/vibravid/db
VIBRAVID_CONFIG_DIR=/share/Container/vibravid/conf
VIBRAVID_LOGS_DIR=/share/Container/vibravid/logs
VIBRAVID_PORT=8000
ALLOWED_HOSTS=localhost,127.0.0.1,<qnap-ip>
CSRF_TRUSTED_ORIGINS=http://<qnap-ip>:8000
```

4. Find your user's UID/GID:

```bash
id <your-username>
```

Add to `.env`:

```env
PUID=<your-uid>
PGID=<your-gid>
```

5. Pull the pre-built ARM64 image and start:

```bash
docker compose pull
docker compose up -d
```

Docker automatically selects the ARM64 image variant from the multi-arch manifest.

6. Access VibraVid at `http://<qnap-ip>:8000`.

---

## Updates

### Manual update

```bash
docker compose pull
docker compose up -d
```

This pulls the latest published image and recreates the container without touching volumes.

### In-app update (if enabled)

When a new version is available, VibraVid will show an update banner in the web UI. Follow the on-screen instructions to apply the update.

---

## Troubleshooting

### "DisallowedHost" or "403 Forbidden"

Your NAS IP is not in `ALLOWED_HOSTS`. Add it:

```env
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.100
CSRF_TRUSTED_ORIGINS=http://192.168.1.100:8000
```

Then recreate the container:

```bash
docker compose up -d --force-recreate
```

### Downloads owned by root

`PUID` / `PGID` are not set or do not match the folder owner. Check with `id <user>` on the NAS host and set the variables accordingly.

### Port already in use

Change `VIBRAVID_PORT` to a free port (e.g., `8080`), update `CSRF_TRUSTED_ORIGINS` to match, and recreate the container.

### Container exits immediately

Check logs for Python errors:

```bash
docker compose logs --tail=50
```

Common causes: invalid `config.json` (delete the conf volume and let the entrypoint re-seed it), missing bind-mount directory (create it on the host first), or a stale database migration (run `docker compose down -v` and start fresh).
