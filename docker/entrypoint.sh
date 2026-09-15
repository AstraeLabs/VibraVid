#!/bin/sh
set -e

# ── 1. PUID / PGID remapping ─────────────────────────────────────────────────
# If PUID or PGID is set and differs from the built-in appuser (1000), remap
# the user so that files written to host-mounted volumes are owned by the
# expected uid/gid. This is the standard pattern for rootless NAS containers.
CURRENT_UID=$(id -u appuser)
CURRENT_GID=$(id -g appuser)

if [ -n "$PUID" ] && [ "$PUID" != "$CURRENT_UID" ]; then
    usermod -u "$PUID" appuser
fi

if [ -n "$PGID" ] && [ "$PGID" != "$CURRENT_GID" ]; then
    groupmod -g "$PGID" appuser
fi

if [ -n "$PUID" ] || [ -n "$PGID" ]; then
    chown -R appuser:appuser /app/Video /app/Conf /app/data /app/logs /app/.cache /home/appuser/.local/bin/binary 2>/dev/null || true
fi

# Create a logs cache directory for the appuser to write to, if it doesn't exist.
mkdir -p /app/.cache/logs
chown appuser:appuser /app/.cache /app/.cache/logs 2>/dev/null || true

# ── 2. First-run Conf seeding ─────────────────────────────────────────────────
if [ -z "$(ls -A /app/Conf 2>/dev/null)" ]; then
    echo "VibraVid: seeding /app/Conf from image defaults..."
    cp -r /app/Conf.defaults/. /app/Conf/
    chown -R appuser:appuser /app/Conf
fi

# ── 2a. Pre-fetch lazily-downloaded binaries (velora, flux) ──────────────────
gosu appuser python -c "
from VibraVid.setup.checker import check_velora, check_flux
check_velora()
check_flux()
" || echo "VibraVid: binary pre-fetch failed, will retry lazily at runtime"

# ── 2b. Docker socket access (in-app updater) ────────────────────────────────
if [ -S /var/run/docker.sock ]; then
    SOCK_GID=$(stat -c '%g' /var/run/docker.sock 2>/dev/null || echo "")
    if [ -n "$SOCK_GID" ] && [ "$SOCK_GID" != "0" ]; then
        if ! getent group "$SOCK_GID" >/dev/null 2>&1; then
            groupadd -g "$SOCK_GID" dockerhost 2>/dev/null || true
        fi
        SOCK_GROUP=$(getent group "$SOCK_GID" | cut -d: -f1)
        [ -n "$SOCK_GROUP" ] && usermod -aG "$SOCK_GROUP" appuser 2>/dev/null || true
    else
        chmod 666 /var/run/docker.sock 2>/dev/null || true
    fi
fi

# ── 3. Migrations ─────────────────────────────────────────────────────────────
gosu appuser python GUI/manage.py migrate --noinput

# ── 4. Start server ───────────────────────────────────────────────────────────
exec gosu appuser python GUI/manage.py runserver --noreload 0.0.0.0:8000
