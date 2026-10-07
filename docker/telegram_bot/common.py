# 26.07.26

import asyncio
import html
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from telethon import Button
from telethon.errors import RPCError

from docker.telegram_bot import state


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)



def _http_json(method: str, path: str, payload: dict | None = None, timeout: float = 120):
    url = state.cfg.gui_url + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None

    headers = {"Content-Type": "application/json"}
    secret = (os.environ.get("VIBRAVID_BOT_SECRET") or "").strip()
    if secret:
        headers["X-VibraVid-Token"] = secret

    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except (OSError, ValueError):
            raise e from None



async def gui_post(path: str, payload: dict, timeout: float = 120):
    return await asyncio.to_thread(_http_json, "POST", path, payload, timeout)



async def gui_get(path: str, timeout: float = 30):
    return await asyncio.to_thread(_http_json, "GET", path, None, timeout)



def _remember(store: OrderedDict, value) -> str:
    token = secrets.token_hex(4)
    store[token] = value
    while len(store) > 64:
        store.popitem(last=False)
    return token



def _load_prefs() -> dict[str, str]:
    try:
        with state.PREFS_FILE.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        state.log.exception("prefs.json unreadable (%s), starting fresh", state.PREFS_FILE)
        return {}



def _save_prefs() -> None:
    try:
        state.PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = state.PREFS_FILE.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state._prefs, fh, ensure_ascii=False, indent=0)
        tmp.replace(state.PREFS_FILE)  # atomic write
    except OSError:
        state.log.exception("Could not save prefs.json")



def get_user_site(user_id: int) -> str:
    return state._prefs.get(str(user_id), state.DEFAULT_SITE)



def set_user_site(user_id: int, value: str) -> None:
    state._prefs[str(user_id)] = value
    _save_prefs()



async def get_site_options(force: bool = False) -> list[dict]:
    """Site list from the GUI (TTL cache). Global option first, then individual sites."""

    now = time.time()
    if not force and state._site_opts and now - state._site_opts_ts < state.SITE_OPTS_TTL:
        return state._site_opts
    try:
        resp = await gui_get("/api/bot/sites/")
    except (OSError, ValueError):
        state.log.exception("Failed to fetch site list")
        return state._site_opts
    groups = resp.get("groups") or []
    groups.sort(key=lambda g: 0 if "glob" in str(g.get("label", "")).lower() else 1)
    flat: list[dict] = []
    for g in groups:
        for o in g.get("options") or []:
            if o.get("value"):
                flat.append({"value": o["value"], "label": o.get("label") or o["value"], "group": g.get("label")})
    if flat:
        state._site_opts = flat
        state._site_opts_ts = now
    return state._site_opts



def _label_for(value: str) -> str:
    """Human-readable label for a site token (uses the cache, with a fallback)."""
    for o in state._site_opts:
        if o["value"] == value:
            return o["label"]
    if value == "__all__":
        return "🌐 All sites"
    if value.startswith("__cat__:"):
        return "🌐 " + value.split(":", 1)[1].replace("_", " ").title()
    return value.replace("_", " ").title()



def _site_view(options: list[dict], selected: str, page: int, token: str) -> tuple[str, list[list[Button]]]:
    pages = max(1, (len(options) + state.PAGE_SIZE - 1) // state.PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    title = "🎯 Where should /scarica search?\nCurrent: <b>" + _esc(_label_for(selected)) + "</b>"
    if pages > 1:
        title += f"\n<i>(page {page + 1}/{pages})</i>"
    buttons: list[list[Button]] = []
    for idx in range(page * state.PAGE_SIZE, min((page + 1) * state.PAGE_SIZE, len(options))):
        o = options[idx]
        mark = "✅ " if o["value"] == selected else ""
        buttons.append([Button.inline((mark + o["label"])[:60], f"ws:{token}:{idx}")])
    nav: list[Button] = []
    if page > 0:
        nav.append(Button.inline("◀️", f"wp:{token}:{page - 1}"))
    if page < pages - 1:
        nav.append(Button.inline("▶️", f"wp:{token}:{page + 1}"))
    if nav:
        buttons.append(nav)
    return title, buttons


def is_allowed(event) -> bool:
    state.cfg.refresh()
    if not state.cfg.restricted:
        return True
    return event.sender_id in state.cfg.allowed_users


async def deny(event) -> None:
    state.log.warning("Access denied: %s", event.sender_id)
    try:
        await event.respond("You are not authorized to use this bot.")
    except (RPCError, OSError):
        pass


def _cb_parts(event) -> list[str]:
    return event.data.decode("utf-8", "replace").split(":")



def _poster_url(item: dict) -> str | None:
    """Poster/thumbnail URL from a search result's payload (TMDB or site-provided), if any."""
    poster = (item.get("payload") or {}).get("poster")
    if not poster or "via.placeholder.com" in poster:
        return None
    return poster


async def _edit_or_upgrade_to_photo(event, item: dict, caption: str, buttons=None):
    """Edits `event`'s message with `caption`/`buttons`."""
    msg_obj = await event.get_message()
    if msg_obj and msg_obj.media:
        return await event.edit(caption, buttons=buttons, parse_mode="html")
    poster = _poster_url(item)
    if poster:
        try:
            msg = await state.client.send_file(
                event.chat_id, poster, caption=caption, buttons=buttons, parse_mode="html"
            )
        except (RPCError, OSError, ValueError):
            state.log.warning("Poster upload failed for %r, falling back to text", item.get("title"))
        else:
            try:
                await event.delete()
            except (RPCError, OSError):
                pass
            return msg
    return await event.edit(caption, buttons=buttons, parse_mode="html")


async def _downgrade_to_text(event, caption: str):
    """Returns a plain-text message with `caption`, converting away from a photo if `event`'s current message is one."""
    msg_obj = await event.get_message()
    if not (msg_obj and msg_obj.media):
        return await event.edit(caption, parse_mode="html")
    msg = await state.client.send_message(event.chat_id, caption, parse_mode="html")
    try:
        await event.delete()
    except (RPCError, OSError):
        pass
    return msg


def _remote_icon(item: dict) -> str:
    t = str(item.get("type") or "").lower()
    if t in ("song", "track", "music", "album"):
        return "🎵"
    if t in ("book", "ebook", "audiobook"):
        return "📚"
    return "🎬" if item.get("is_movie") else "📺"


_MEDIA_KIND_LABELS = {
    "movie": "Movie", "series": "Series", "song": "Song",
    "album": "Album", "book": "Book", "live": "Live",
}


def _type_label(item: dict) -> str:
    """Normalized, human-readable media type for search-result buttons (Movie/Series/...)."""
    kind = str(item.get("media_kind") or "").strip().lower()
    if kind:
        return _MEDIA_KIND_LABELS.get(kind, kind.capitalize())
    return "Movie" if item.get("is_movie") else "Series"



def _fmt_bytes(n) -> str:
    """Bytes into a human-readable unit (GB/TB), for /stato."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"



async def _do_scarica_search(event, query: str) -> None:
    """Search across providers respecting the user's site filter (/sito)."""
    state._last_query[event.sender_id] = query
    site = get_user_site(event.sender_id)
    if not state._site_opts:
        await get_site_options()
    scope = _label_for(site)

    waiting = await state.client.send_message(
        event.chat_id, f"🔍 Searching <b>{_esc(query)}</b> on {_esc(scope)}… (up to ~45s)", parse_mode="html"
    )
    try:
        resp = await gui_post("/api/bot/search/", {"query": query, "site": site}, timeout=90)
    except (OSError, ValueError):
        state.log.exception("GUI search failed")
        await waiting.edit("❌ Can't reach VibraVid. Is the 'vibravid' container up?", parse_mode=None)
        return

    if resp.get("error"):
        await waiting.edit(f"❌ VibraVid: {_esc(resp['error'])}", parse_mode="html")
        return
    results = (resp.get("results") or [])[:12]
    failed = resp.get("failed") or []
    note = f"\n<i>(no response from: {_esc(', '.join(failed))})</i>" if failed else ""
    if not results:
        await waiting.edit(
            f"No results for <b>{_esc(query)}</b> on {_esc(scope)}.{note}\n"
            "Change sites with /sito, or check what happened with /log.",
            parse_mode="html",
        )
        return

    token = _remember(state._REMOTE, results)
    buttons = []
    for i, r in enumerate(results):
        year = f" ({r['year']})" if r.get("year") else ""
        label = f"{_remote_icon(r)} {r.get('title', '?')}{year} · {_type_label(r)} · {r.get('site', '?')}"
        buttons.append([Button.inline(label[:60], f"g:{token}:{i}")])

    header = f"🔎 Results for <b>{_esc(query)}</b> on {_esc(scope)}:{note}"
    await waiting.edit(header, buttons=buttons, parse_mode="html")



async def _start_remote_download(event, item: dict, season: str | None, episodes: str | None, expected: int = 1) -> None:
    """Starts the download via the GUI and registers it for monitoring (status text in chat, no file ever sent)."""
    payload = {
        "site": item.get("site"),
        "payload": item.get("payload") or {},
        "season": season,
        "episodes": episodes,
    }
    try:
        resp = await gui_post("/api/bot/download/", payload, timeout=60)
    except (OSError, ValueError):
        state.log.exception("Failed to start download")
        await event.edit("❌ Error starting the download on VibraVid.")
        return
    if resp.get("error"):
        await event.edit(f"❌ VibraVid: {_esc(resp['error'])}", parse_mode="html")
        return

    title = resp.get("title") or item.get("title") or "?"
    download_id = resp.get("download_id") or f"tg_{secrets.token_hex(4)}"
    caption = f"🕐 Queued: <b>{_esc(title)}</b>"
    msg = await _downgrade_to_text(event, caption)
    state._pending[download_id] = {
        "title": title,
        "chat_id": event.chat_id,
        "ts": time.time(),
        "msg": msg,
        "last_text": "",
        "expected": max(1, expected),
        "was_active": False,
    }
    state.log.info("Download requested: %s (id=%s, expected=%d)", title, download_id, max(1, expected))



def _episode_view(title: str, token: str, idx: str, season: str, count: int, page: int) -> tuple[str, list[list[Button]]]:
    """Buttons for a season's episode picker: 'all' + a numbered grid (paginated like /sito)."""
    count = max(0, count)
    pages = max(1, (count + state.EP_PAGE_SIZE - 1) // state.EP_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    head = f"📺 <b>{_esc(title)}</b> — Season {season}: which episode?"
    if pages > 1:
        head += f"\n<i>(page {page + 1}/{pages})</i>"

    buttons: list[list[Button]] = [
        [Button.inline(f"📥 All ({count} episodes)", f"sa:{token}:{idx}:{season}:{count}")],
    ]

    start = page * state.EP_PAGE_SIZE + 1
    end = min((page + 1) * state.EP_PAGE_SIZE, count)
    row: list[Button] = []
    for ep in range(start, end + 1):
        row.append(Button.inline(str(ep), f"se:{token}:{idx}:{season}:{ep}"))
        if len(row) == state.EP_COLS:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    nav: list[Button] = []
    if page > 0:
        nav.append(Button.inline("◀️", f"sp:{token}:{idx}:{season}:{count}:{page - 1}"))
    if page < pages - 1:
        nav.append(Button.inline("▶️", f"sp:{token}:{idx}:{season}:{count}:{page + 1}"))
    if nav:
        buttons.append(nav)

    return head, buttons


async def _send_log_text(event, header: str, text: str) -> None:
    limit = 3500
    if len(text) > limit:
        text = text[-limit:]
        header += " (truncated to the last lines)"
    await state.client.send_message(event.chat_id, f"{header}\n\n{text}")


__all__ = [
    '_esc', '_http_json', 'gui_post', 'gui_get', '_remember', '_load_prefs',
    '_save_prefs', 'get_user_site', 'set_user_site', 'get_site_options',
    '_label_for', '_site_view', 'is_allowed', 'deny', '_cb_parts',
    '_poster_url', '_edit_or_upgrade_to_photo', '_downgrade_to_text',
    '_remote_icon', '_fmt_bytes', '_do_scarica_search', '_start_remote_download',
    '_episode_view', '_send_log_text',
]
