# TGBot — Telegram "trigger-download" bot

Triggers VibraVid downloads from Telegram chat. **It never sends files**: it searches, starts the download and reports status as text (queued -> downloading NN% -> completed, with the saved path). The file stays on the VibraVid machine; fetch it with SMB, Syncthing, SFTP, etc.

It uses Telethon (MTProto), already used elsewhere, so you need `api_id` / `api_hash` (free at https://my.telegram.org) besides the BotFather `bot_token`.

## Configuration

**Credentials** (`bot_token`, `api_id`, `api_hash`, `allowed_users`) come **only** from `TG_*` environment variables, typically a dedicated `.env.telegram` (copy `.env.telegram.example`) loaded by the `telegram` service in `docker-compose.yml`. No fallback to `login.toml`/`config.toml`; changing them needs a container restart.

**Non-sensitive settings** live in `[TELEGRAM]` of `Conf/config.toml` (hot-reloaded), falling back to the `TG_*` variables:

```toml
[TELEGRAM]
enabled = false
gui_url = "http://vibravid:8000"
max_results = 8
```

| Variable | Required | Description |
|---|---|---|
| `TG_BOT_TOKEN` | yes | Token from BotFather |
| `TG_API_ID` / `TG_API_HASH` | yes | From https://my.telegram.org |
| `TG_ALLOWED_USERS` | recommended | Comma-separated numeric Telegram IDs allowed to use the bot; empty = everyone |
| `TG_GUI_URL` | no | Internal GUI URL (default `http://vibravid:8000`) |
| `VIBRAVID_BOT_SECRET` | no | Shared secret authenticating the bot to the GUI (`X-VibraVid-Token`); must also be set on the GUI side |

## Commands

| Command | Description |
|---|---|
| `/scarica <title>` | Search the configured sites (respecting `/sito`) and show results as buttons. Movies/songs/albums start at once; series ask for a season, then an episode or the whole season. Without arguments: asks title, then site |
| `/sito` (`/siti`, `/filtro`) | Choose what `/scarica` searches: "🌐 All sites" (default), a category (Movies/Series/Anime/Music) or one site. Saved per user; a "🔍 Repeat" button reruns the last search with the new filter |
| `/coda` (`/downloads`) | Active downloads (with %) and queued ones |
| `/annulla` | Your in-progress downloads as buttons to cancel |
| `/stato` (`/status`) | Free/total disk space and active/max download slots |
| `/log` | Latest search/download log lines. Variants: `/log 100` (line count), `/log err` (errors/warnings), `/log bot` (the bot's in-memory log, cleared on restart) |

While downloading, the message updates with the percentage and a "❌ Cancel download" button; it ends with "✅ completed" (and the path) or a summary of failures / already-in-library items.

## Bridge to the GUI

The bot calls the GUI over HTTP (`gui_url`) using the [`api/bot/*` endpoints](gui.md#bot-remote-control-api) plus `/api/get-downloads/` for `/coda`. With `VIBRAVID_BOT_SECRET` set, every request carries `X-VibraVid-Token`; without it the endpoints are unauthenticated, so set it before exposing the GUI beyond your LAN.

## Running it

Dedicated container (`telegram` in `docker-compose.yml`): same image as `vibravid`, entrypoint `docker/telegram-entrypoint.sh`, which runs `python docker/telegram_bot/bot.py` as a script (not `-m`: imports are absolute, see `PYTHONPATH=/app` in the Dockerfile).
