# TGBot — Telegram "trigger-download" bot for VibraVid

Telegram bot that triggers downloads on VibraVid from chat. **It never sends
files over Telegram**: it searches, starts the download, and reports status as
text (queued → downloading NN% → completed, with the saved file's path). The
finished file stays on the disk of the machine running VibraVid; to retrieve
it you need another tool that can reach that disk/NAS (SMB, Syncthing, an
SFTP client, etc.) — this bot only covers triggering the download remotely.

## Why Telethon

It uses Telethon (MTProto) instead of the HTTP Bot API — not for the file
size limit (irrelevant here, since no files are sent), but because it's the
same client already used for the other commands, avoiding a second library.
This means you need `api_id` / `api_hash` (free at
https://my.telegram.org) in addition to the `bot_token` from BotFather.

## Configuration

**Credentials** (`bot_token`, `api_id`, `api_hash`, `allowed_users`) are read
**only** from `TG_*` environment variables, typically from a dedicated
`.env.telegram` file (copied from `.env.telegram.example`) loaded by the
`telegram` service in `docker-compose.yml`. There is no fallback to
`Conf/login.json` or `Conf/config.json`: they're intentionally kept separate
from the rest of VibraVid's configuration. Changing them requires restarting
the container.

**Non-sensitive settings** (`enabled`, `gui_url`, `max_results`) live instead
in the `TELEGRAM` section of `Conf/config.json`, hot-reloaded (no restart
needed), falling back to the same `TG_*` variables if absent there.

```json
"TELEGRAM": {
    "enabled": false,
    "gui_url": "http://vibravid:8000",
    "max_results": 8
}
```

Relevant environment variables (see `.env.telegram.example`):

| Variable | Required | Description |
|---|---|---|
| `TG_BOT_TOKEN` | yes | Token from BotFather |
| `TG_API_ID` / `TG_API_HASH` | yes | From https://my.telegram.org |
| `TG_ALLOWED_USERS` | recommended | Comma-separated numeric Telegram IDs allowed to use the bot; empty = everyone. |
| `TG_GUI_URL` | no | Internal URL of the VibraVid GUI (default `http://vibravid:8000`) |
| `VIBRAVID_BOT_SECRET` | no | Shared secret used to authenticate the bot against the GUI (`X-VibraVid-Token`); optional, must also be set on the GUI side if used |

## Commands

- **`/scarica <title>`** — searches the configured sites (respecting the
  `/sito` filter) and shows results as buttons. Movies/songs/albums start
  immediately; series show a season selector first, then a single episode or
  the whole season. Without arguments, the bot first asks for the title, then
  the site (guided flow).
- **`/sito`** (alias `/siti`, `/filtro`) — choose which sites `/scarica`
  searches: "🌐 All sites" (default), a category (Movies/Series/Anime/Music),
  or a single site. The choice persists per user. If there's a recent search,
  a "🔍 Repeat" button appears to rerun it with the new filter.
- **`/coda`** (alias `/downloads`) — active downloads (with percentage) and
  queued ones.
- **`/annulla`** — lists your in-progress downloads as buttons to cancel
  them.
- **`/stato`** (alias `/status`) — free/total disk space and active/max
  download slots on VibraVid.
- **`/log`** — latest search/download log lines (from the GUI). Combinable
  variants: `/log 100` (line count), `/log err` (errors/warnings only),
  `/log bot` (the bot's own in-memory log, cleared on container restart).

While a download is in progress, the message updates itself with the
percentage and a "❌ Cancel download" button; at the end it closes with
"✅ completed" (and the file path), or a summary if something failed or was
already in the library.

## Bridge to the GUI

The bot talks to the VibraVid GUI over HTTP (`gui_url`), via dedicated
endpoints under `GUI/searchapp/api_bot.py` (`/api/bot/search/`,
`/api/bot/seasons/`, `/api/bot/download/`, `/api/bot/sites/`,
`/api/bot/cancel/`, `/api/bot/status/`, `/api/bot/logs/`), plus the existing
generic endpoint `/api/get-downloads/` for `/coda`. If `VIBRAVID_BOT_SECRET`
is set, every request includes the `X-VibraVid-Token` header with the same
value:

```bash
curl -X POST "http://gui:8000/api/bot/search/" \
  -H "X-VibraVid-Token: <same value as VIBRAVID_BOT_SECRET>" \
  -H "Content-Type: application/json" \
  -d '{"query": "interstellar", "site": "__all__"}'
```

Without a matching (or any) `VIBRAVID_BOT_SECRET` set on the GUI, these endpoints accept
unauthenticated requests — set it before exposing the GUI beyond your local network.

## Running it

Dedicated Docker container (`telegram` in `docker-compose.yml`), same image
as `vibravid` but with a different entrypoint (`docker/telegram-entrypoint.sh`),
which launches `python docker/telegram_bot/bot.py` as a script (not `-m`:
package imports are absolute, see `PYTHONPATH=/app` in the Dockerfile).
