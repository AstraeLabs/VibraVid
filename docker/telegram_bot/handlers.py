# 26.07.26

import urllib.parse

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from docker.telegram_bot import state
from docker.telegram_bot.common import (
    _cb_parts,
    _do_scarica_search,
    _edit_or_upgrade_to_photo,
    _episode_view,
    _esc,
    _fmt_bytes,
    _remember,
    _send_log_text,
    _site_view,
    _start_remote_download,
    deny,
    get_site_options,
    get_user_site,
    gui_get,
    gui_post,
    is_allowed,
    set_user_site,
)


async def cmd_start(event) -> None:
    if not is_allowed(event):
        return await deny(event)
    await event.respond(
        "👋 Hi! Send me <b>/scarica</b> followed by a title (movie, series, anime, "
        "music, book) and I'll start the download on VibraVid. The file stays on the "
        "server's disk: this bot never sends files, only status text.\n\n"
        "<b>Commands</b>\n"
        "<b>/scarica</b> <code>&lt;title&gt;</code> — search sites and download with VibraVid\n"
        "<b>/coda</b> — active and queued downloads · <b>/annulla</b> — stop a download\n"
        "<b>/stato</b> — disk space and VibraVid health\n"
        "<b>/sito</b> — choose which sites to search (default: all)\n"
        "<b>/log</b> — search/download logs (<code>/log 100</code>, <code>/log err</code>, <code>/log bot</code>)",
        parse_mode="html",
    )
    raise events.StopPropagation



async def cmd_scarica(event) -> None:
    if not is_allowed(event):
        return await deny(event)
    query = (event.pattern_match.group(2) or "").strip()
    if not query:
        state._awaiting_scarica_query.add(event.sender_id)
        await event.respond("✏️ What do you want to download? Send me the title.")
        raise events.StopPropagation
    await _do_scarica_search(event, query)
    raise events.StopPropagation



async def on_scarica_query_text(event) -> None:
    sid = event.sender_id
    if sid not in state._awaiting_scarica_query:
        return
    text = (event.raw_text or "").strip()
    if not text or text.startswith("/"):
        return
    if not is_allowed(event):
        return
    state._awaiting_scarica_query.discard(sid)

    options = await get_site_options(force=True)
    if not options:
        await event.respond("⚠️ Can't read the site list, searching with the current filter…")
        await _do_scarica_search(event, text)
        raise events.StopPropagation

    state._awaiting_site_search[sid] = text  # on_site_pick will fire the search once a site is chosen
    token = _remember(state._SITEOPTS_STORE, options)
    title, buttons = _site_view(options, get_user_site(sid), 0, token)
    title = f"🔎 Searching: <b>{_esc(text)}</b>\nWhich site?\n\n" + title
    await event.respond(title, buttons=buttons, parse_mode="html")
    raise events.StopPropagation



async def on_remote_pick(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token, idx = _cb_parts(event)
        item = state._REMOTE[token][int(idx)]
    except (KeyError, IndexError, ValueError):
        await event.edit("Selection expired. Redo /scarica.")
        return

    t = str(item.get("type") or "").lower()
    if item.get("is_movie") or t in ("song", "track", "music", "album", "film", "movie", "book", "ebook", "audiobook"):
        await _start_remote_download(event, item, None, None)
        return

    title = item.get("title", "?")
    await event.edit(f"📺 <b>{_esc(title)}</b> — loading seasons…", parse_mode="html")
    try:
        resp = await gui_post(
            "/api/bot/seasons/",
            {"site": item.get("site"), "payload": item.get("payload") or {}},
            timeout=90,
        )
        seasons = resp.get("seasons") or []
    except (OSError, ValueError):
        state.log.exception("Series metadata fetch failed")
        seasons = []
    if not seasons:
        await event.edit("❌ Can't read the seasons. Try again or use the GUI.")
        return

    buttons = [
        [Button.inline(
            f"📅 Season {s['number']} ({s.get('episodes', 0)} ep)",
            f"s:{token}:{int(idx)}:{s['number']}:{int(s.get('episodes') or 0)}",
        )]
        for s in seasons[:20]
    ]
    await _edit_or_upgrade_to_photo(
        event, item, f"📺 <b>{_esc(title)}</b> — which season?", buttons=buttons,
    )



async def on_season_pick(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token, idx, season, count = _cb_parts(event)
        item = state._REMOTE[token][int(idx)]
    except (KeyError, IndexError, ValueError):
        await event.edit("Selection expired. Redo /scarica.")
        return
    head, buttons = _episode_view(item.get("title", "?"), token, idx, season, int(count or 0), 0)
    await event.edit(head, buttons=buttons, parse_mode="html")



async def on_episode_page(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token, idx, season, count, page = _cb_parts(event)
        item = state._REMOTE[token][int(idx)]
    except (KeyError, IndexError, ValueError):
        await event.edit("Selection expired. Redo /scarica.")
        return
    head, buttons = _episode_view(item.get("title", "?"), token, idx, season, int(count or 0), int(page))
    try:
        await event.edit(head, buttons=buttons, parse_mode="html")
    except MessageNotModifiedError:
        pass



async def on_season_all(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token, idx, season, count = _cb_parts(event)
        item = state._REMOTE[token][int(idx)]
    except (KeyError, IndexError, ValueError):
        await event.edit("Selection expired. Redo /scarica.")
        return
    expected = int(count) if str(count).isdigit() and int(count) > 0 else 1
    await _start_remote_download(event, item, season, "*", expected=expected)



async def on_episode_pick(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token, idx, season, episode = _cb_parts(event)
        item = state._REMOTE[token][int(idx)]
    except (KeyError, IndexError, ValueError):
        await event.edit("Selection expired. Redo /scarica.")
        return
    await _start_remote_download(event, item, season, episode, expected=1)



async def cmd_sito(event) -> None:
    if not is_allowed(event):
        return await deny(event)
    options = await get_site_options(force=True)
    if not options:
        await event.respond(
            "Can't read the site list from the GUI. "
            "Is the 'vibravid' container up? Try again, or check /log."
        )
        raise events.StopPropagation
    selected = get_user_site(event.sender_id)
    token = _remember(state._SITEOPTS_STORE, options)
    title, buttons = _site_view(options, selected, 0, token)
    await event.respond(title, buttons=buttons, parse_mode="html")
    raise events.StopPropagation



async def on_site_page(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token, page = _cb_parts(event)
        options = state._SITEOPTS_STORE[token]
    except (KeyError, ValueError):
        await event.edit("Menu expired. Redo /sito.")
        return
    title, buttons = _site_view(options, get_user_site(event.sender_id), int(page), token)
    try:
        await event.edit(title, buttons=buttons, parse_mode="html")
    except MessageNotModifiedError:
        pass



async def on_site_pick(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token, idx = _cb_parts(event)
        options = state._SITEOPTS_STORE[token]
        opt = options[int(idx)]
    except (KeyError, IndexError, ValueError):
        await event.edit("Menu expired. Redo /sito.")
        return
    set_user_site(event.sender_id, opt["value"])
    state.log.info("User %s: site filter = %s", event.sender_id, opt["value"])

    pending_query = state._awaiting_site_search.pop(event.sender_id, None)
    if pending_query:
        await _do_scarica_search(event, pending_query)
        return

    page = int(idx) // state.PAGE_SIZE
    title, buttons = _site_view(options, opt["value"], page, token)
    title = f"✅ /scarica now searches: <b>{_esc(opt['label'])}</b>\n\n" + title
    lastq = state._last_query.get(event.sender_id)
    if lastq:
        buttons = [[Button.inline(f"🔍 Repeat: {lastq[:38]}", "rs")]] + buttons
    try:
        await event.edit(title, buttons=buttons, parse_mode="html")
    except MessageNotModifiedError:
        pass



async def on_research(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    query = state._last_query.get(event.sender_id)
    if not query:
        await event.edit("No recent search. Use /scarica <title>.")
        return
    await _do_scarica_search(event, query)



async def cmd_log(event) -> None:
    if not is_allowed(event):
        return await deny(event)
    raw = (event.pattern_match.group(1) or "").strip()

    lines = 200
    level = "all"
    source = "gui"
    for part in raw.split():
        p = part.lower()
        if p.isdigit():
            lines = max(1, min(int(p), 500))
        elif p in ("err", "error", "errors", "errori", "e"):
            level = "err"
        elif p in ("bot", "tg", "tgbot"):
            source = "bot"
        elif p in ("gui", "vibravid", "vv", "download", "ricerca", "search"):
            source = "gui"

    err_tag = " — errors only" if level == "err" else ""

    if source == "bot":
        buf = list(state._LOGBUF)
        if level == "err":
            buf = [row for row in buf if any(k in row for k in ("ERROR", "WARNING", "CRITICAL"))]
        text = "\n".join(buf[-lines:]) or "(no bot logs)"
        await _send_log_text(event, f"🤖 Bot log (last {min(lines, len(buf))} lines{err_tag})", text)
        raise events.StopPropagation

    q = urllib.parse.urlencode({"lines": lines, "level": level})
    try:
        resp = await gui_get(f"/api/bot/logs/?{q}")
    except (OSError, ValueError):
        state.log.exception("GUI logs failed")
        await event.respond(
            "❌ Can't read logs from the GUI (is the 'vibravid' container up?).\n"
            "You can see the bot's own logs with /log bot."
        )
        raise events.StopPropagation from None
    if resp.get("error"):
        await event.respond(f"❌ VibraVid: {resp['error']}")
        raise events.StopPropagation

    gl = resp.get("lines") or []
    if not gl:
        note = resp.get("note") or "no lines"
        await event.respond(f"📄 VibraVid log: {note}.")
        raise events.StopPropagation
    fname = resp.get("file") or "vibravid"
    text = "\n".join(gl)
    await _send_log_text(event, f"📄 VibraVid log ({fname}) — last {len(gl)} lines{err_tag}", text)
    raise events.StopPropagation



async def on_cancel_download(event) -> None:
    await event.answer()
    if not is_allowed(event):
        return
    try:
        _, token = _cb_parts(event)
        did = state._CANCEL_TOKENS.get(token)
    except ValueError:
        return
    if not did:
        await event.edit("Request expired.")
        return
    info = state._pending.pop(did, None)
    try:
        # download_id is enough: the endpoint resolves the series and clears the rest of the queue too.
        resp = await gui_post("/api/bot/cancel/", {"download_id": did}, timeout=30)
    except (OSError, ValueError):
        state.log.exception("Failed to cancel download")
        await event.edit("⚠️ Can't cancel (GUI unreachable).")
        return
    if resp.get("error"):
        await event.edit(f"⚠️ Cancel failed: {_esc(resp['error'])}", parse_mode="html")
        return
    title = _esc(info["title"]) if info else "download"
    await event.edit(f"❌ Cancelled: <b>{title}</b>.", parse_mode="html")



async def cmd_annulla(event) -> None:
    if not is_allowed(event):
        return await deny(event)
    mine = [(did, info) for did, info in state._pending.items() if info.get("chat_id") == event.chat_id]
    if not mine:
        await event.respond("No download in progress to cancel.")
        raise events.StopPropagation
    buttons = []
    for did, info in mine:
        ctok = info.get("cancel_token")
        if not ctok:
            ctok = _remember(state._CANCEL_TOKENS, did)
            info["cancel_token"] = ctok
        buttons.append([Button.inline(f"❌ {info['title']}"[:60], f"dc:{ctok}")])
    await event.respond("Which download do you want to cancel?", buttons=buttons)
    raise events.StopPropagation



async def cmd_coda(event) -> None:
    if not is_allowed(event):
        return await deny(event)
    try:
        data = await gui_get("/api/get-downloads/")
    except (OSError, ValueError):
        state.log.exception("Failed to fetch queue status")
        await event.respond("❌ Can't read the status (is the 'vibravid' container up?).")
        raise events.StopPropagation from None

    active = data.get("active") or []
    scheduled = data.get("scheduled") or []
    if not active and not scheduled:
        await event.respond("📭 No active or queued downloads.")
        raise events.StopPropagation

    lines = []
    if active:
        lines.append("⏬ <b>In progress:</b>")
        for a in active:
            prog = float(a.get("progress") or 0)
            title = _esc(a.get("series_name") or a.get("title") or "?")
            lines.append(f"  • {title} — {prog:.0f}%")
    if scheduled:
        lines.append("\n🕐 <b>Queued:</b>")
        for s in scheduled[:20]:
            lines.append(f"  • {_esc(s.get('title') or '?')}")
        if len(scheduled) > 20:
            lines.append(f"  … and {len(scheduled) - 20} more")
    lines.append("\n<i>To cancel: /annulla</i>")
    await event.respond("\n".join(lines), parse_mode="html")
    raise events.StopPropagation



async def cmd_stato(event) -> None:
    if not is_allowed(event):
        return await deny(event)
    try:
        data = await gui_get("/api/bot/status/")
    except (OSError, ValueError):
        state.log.exception("Health status fetch failed")
        await event.respond("❌ VibraVid unreachable (is the 'vibravid' container down?).")
        raise events.StopPropagation from None

    disk = data.get("disk") or {}
    slots = data.get("slots") or {}
    lines = ["🩺 <b>VibraVid status</b>", "  • Container: ✅ reachable"]
    if disk.get("error"):
        lines.append(f"  • Disk: ⚠️ {_esc(disk['error'])}")
    elif disk.get("free") is not None:
        free = _fmt_bytes(disk.get("free"))
        total = _fmt_bytes(disk.get("total"))
        pct = 100 * disk.get("free", 0) / disk["total"] if disk.get("total") else 0
        warn = " ⚠️" if pct < 10 else ""
        lines.append(f"  • Free disk: {free} / {total} ({pct:.0f}%){warn}")
    lines.append(f"  • Downloads: {slots.get('active', 0)}/{slots.get('max', 1)} active slots · {data.get('queued', 0)} queued")
    await event.respond("\n".join(lines), parse_mode="html")
    raise events.StopPropagation


__all__ = [
    'cmd_start', 'cmd_scarica', 'on_scarica_query_text', 'on_remote_pick',
    'on_season_pick', 'on_episode_page', 'on_season_all', 'on_episode_pick',
    'cmd_sito', 'on_site_page', 'on_site_pick', 'on_research', 'cmd_log',
    'on_cancel_download', 'cmd_annulla', 'cmd_coda', 'cmd_stato',
]
