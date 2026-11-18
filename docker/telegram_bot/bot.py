# 26.07.26

import asyncio

from telethon import TelegramClient, events
from telethon.errors import RPCError
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

from docker.telegram_bot import state
from docker.telegram_bot.common import _load_prefs
from docker.telegram_bot.handlers import (
    cmd_annulla,
    cmd_coda,
    cmd_log,
    cmd_scarica,
    cmd_sito,
    cmd_start,
    cmd_stato,
    on_cancel_download,
    on_episode_page,
    on_episode_pick,
    on_remote_pick,
    on_research,
    on_scarica_query_text,
    on_season_all,
    on_season_pick,
    on_site_page,
    on_site_pick,
)
from docker.telegram_bot.loops import download_monitor_loop


async def main() -> None:

    state.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state._prefs = await asyncio.to_thread(_load_prefs)

    while True:
        state.cfg.refresh()
        if not state.cfg.enabled:
            state.log.info("TELEGRAM.enabled=false: bot paused (rechecking in 60s)")
        elif not (state.cfg.bot_token and state.cfg.api_id and state.cfg.api_hash):
            state.log.warning(
                "Incomplete config: need TG_BOT_TOKEN, TG_API_ID and TG_API_HASH "
                "(see .env.telegram.example). api_id/api_hash: https://my.telegram.org. "
                "Rechecking in 60s."
            )
        else:
            break
        await asyncio.sleep(60)

    state.client = TelegramClient(str(state.STATE_DIR / "telegram_bot"), state.cfg.api_id, state.cfg.api_hash)
    await state.client.start(bot_token=state.cfg.bot_token)
    state.client.parse_mode = None

    state.client.add_event_handler(cmd_start, events.NewMessage(incoming=True, pattern=r"^/(start|help)(@\w+)?$"))
    state.client.add_event_handler(cmd_scarica, events.NewMessage(incoming=True, pattern=r"^/scarica(@\w+)?(?:\s+(.+))?$"))
    state.client.add_event_handler(cmd_sito, events.NewMessage(incoming=True, pattern=r"^/(?:sito|siti|filtro)(?:@\w+)?$"))
    state.client.add_event_handler(cmd_log, events.NewMessage(incoming=True, pattern=r"^/log(?:@\w+)?(?:\s+(.+))?$"))
    state.client.add_event_handler(cmd_coda, events.NewMessage(incoming=True, pattern=r"^/(?:coda|downloads?)(?:@\w+)?$"))
    state.client.add_event_handler(cmd_annulla, events.NewMessage(incoming=True, pattern=r"^/annulla(?:@\w+)?$"))
    state.client.add_event_handler(cmd_stato, events.NewMessage(incoming=True, pattern=r"^/(?:stato|status)(?:@\w+)?$"))
    state.client.add_event_handler(on_scarica_query_text, events.NewMessage(incoming=True))
    state.client.add_event_handler(on_remote_pick, events.CallbackQuery(pattern=rb"^g:"))
    state.client.add_event_handler(on_season_pick, events.CallbackQuery(pattern=rb"^s:"))
    state.client.add_event_handler(on_episode_page, events.CallbackQuery(pattern=rb"^sp:"))
    state.client.add_event_handler(on_season_all, events.CallbackQuery(pattern=rb"^sa:"))
    state.client.add_event_handler(on_episode_pick, events.CallbackQuery(pattern=rb"^se:"))
    state.client.add_event_handler(on_site_page, events.CallbackQuery(pattern=rb"^wp:"))
    state.client.add_event_handler(on_site_pick, events.CallbackQuery(pattern=rb"^ws:"))
    state.client.add_event_handler(on_research, events.CallbackQuery(pattern=rb"^rs$"))
    state.client.add_event_handler(on_cancel_download, events.CallbackQuery(pattern=rb"^dc:"))

    try:
        await state.client(SetBotCommandsRequest(
            scope=BotCommandScopeDefault(),
            lang_code="",
            commands=[
                BotCommand("scarica", "Search sites and download"),
                BotCommand("coda", "Active and queued downloads"),
                BotCommand("annulla", "Cancel an in-progress download"),
                BotCommand("stato", "Disk space and VibraVid health"),
                BotCommand("sito", "Choose which sites to search"),
                BotCommand("log", "Search/download logs"),
                BotCommand("help", "Help"),
            ],
        ))
    except (RPCError, OSError):
        state.log.exception("SetBotCommands failed (non-critical)")

    state.log.info("Bot started (trigger-download only: no file is ever sent).")

    monitor = asyncio.create_task(download_monitor_loop())
    try:
        await state.client.run_until_disconnected()
    finally:
        monitor.cancel()


if __name__ == "__main__":
    asyncio.run(main())
