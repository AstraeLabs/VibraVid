FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nano \
    libicu-dev \
    ffmpeg \
    curl \
    unzip \
    gosu \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r appuser && \
    useradd -r -g appuser -u 1000 -m -d /home/appuser -s /bin/bash appuser

# Docker CLI + Compose plugin — used by the in-app "Update now"
RUN install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" \
        > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Binary directory — lazily-downloaded velora/flux land here at runtime
RUN mkdir -p /home/appuser/.local/bin/binary

# Fix ownership of the entire home directory before switching user
RUN chown -R appuser:appuser /home/appuser/.local

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY GUI/requirements.txt ./GUI/requirements.txt
RUN pip install --no-cache-dir -r GUI/requirements.txt

# Telegram bot deps (telethon/cryptg)
COPY docker/telegram_bot/requirements.txt ./docker/telegram_bot/requirements.txt
RUN pip install --no-cache-dir -r docker/telegram_bot/requirements.txt

# Copy application code
COPY . .

# Snapshot the default Conf directory so the entrypoint can seed it on first start
# when the Conf volume is empty (e.g., fresh NAS install).
RUN cp -r /app/Conf /app/Conf.defaults

# Install entrypoint script
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create required directories and set permissions
# NOTE: the entrypoint runs as root first to handle PUID/PGID remapping,
# so we do not switch to USER appuser here.
RUN mkdir -p /app/Video /app/logs /app/data /app/.cache \
             /home/appuser/.config && \
    chown -R appuser:appuser /app /home/appuser && \
    chmod -R 755 /app /home/appuser

# Set environment variables
ENV PYTHONPATH=/app \
    HOME=/home/appuser \
    DJANGO_DB_DIR=/app/data \
    PYTHONUNBUFFERED=1

# NOTE: no VOLUME directive on purpose. The persistence strategy is declared
# exclusively in docker-compose.yml using NAMED volumes. A VOLUME directive
# here would:
#   1. Create anonymous volumes (random hash names like e93fdjvndss87...) any
#      time the image is started without an explicit -v mount for that path,
#      and those orphan volumes pile up across rebuilds.
#   2. Snapshot the directory content at build time and overlay it on startup,
#      which silently hides image updates inside long-lived volumes.
# Keeping volume management in docker-compose alone makes the setup
# predictable and easy to reset (`docker-compose down -v`).

EXPOSE 8000

# The entrypoint handles PUID/PGID remapping, first-run Conf seeding,
# and Django migrations, then execs the server as appuser via gosu.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
