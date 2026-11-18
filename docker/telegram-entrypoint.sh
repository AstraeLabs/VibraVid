#!/bin/sh
set -e

CURRENT_UID=$(id -u appuser)
CURRENT_GID=$(id -g appuser)

if [ -n "$PUID" ] && [ "$PUID" != "$CURRENT_UID" ]; then
    usermod -u "$PUID" appuser
fi

if [ -n "$PGID" ] && [ "$PGID" != "$CURRENT_GID" ]; then
    groupmod -g "$PGID" appuser
fi

mkdir -p /app/tgstate
chown -R appuser:appuser /app/tgstate 2>/dev/null || true

exec gosu appuser python docker/telegram_bot/bot.py
