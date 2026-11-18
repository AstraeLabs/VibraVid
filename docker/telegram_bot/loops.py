# 26.07.26

import asyncio
import time

from telethon import Button
from telethon.errors import MessageNotModifiedError, RPCError

from docker.telegram_bot import state
from docker.telegram_bot.common import _esc, _remember, gui_get


async def download_monitor_loop() -> None:
    """Follows downloads triggered via /scarica by polling the GUI."""
    while True:
        await asyncio.sleep(1)
        if not state._pending:
            continue
        try:
            data = await gui_get("/api/get-downloads/")
        except (OSError, ValueError):
            continue

        active = data.get("active") or []
        scheduled = data.get("scheduled") or []
        history = data.get("history") or []

        for did, info in list(state._pending.items()):
            try:
                def match(entry, did=did, info=info) -> bool:
                    return entry.get("id") == did or entry.get("title") == info["title"]

                matches = [h for h in history if match(h) and h.get("end_time", 0) >= info["ts"]]
                run = next((a for a in active if match(a)), None)
                still_running = run is not None or any(match(s) for s in scheduled)

                completed_paths = {
                    h["path"] for h in matches
                    if h.get("status") == "completed" and h.get("path")
                }
                failed = [h for h in matches if h.get("status") != "completed"]

                sig = (len(matches), still_running)
                if sig != info.get("_sig"):
                    info["_sig"] = sig
                    info["_last_change"] = time.time()

                # Never showed up in history, nor active/queued, for >2 minutes: lost.
                if not still_running and not matches and time.time() - info["ts"] > 120:
                    state._pending.pop(did, None)
                    try:
                        await info["msg"].edit(
                            f"⚠️ <b>{_esc(info['title'])}</b>: no longer among the downloads. Check the GUI.",
                            parse_mode="html",
                        )
                    except (RPCError, OSError):
                        pass
                    continue

                if still_running:
                    info["was_active"] = True
                    info.pop("finalizing_since", None)

                    # Cancel button: stable token so it doesn't change on every edit.
                    ctok = info.get("cancel_token")
                    if not ctok:
                        ctok = _remember(state._CANCEL_TOKENS, did)
                        info["cancel_token"] = ctok
                    
                    cancel_btn = [[Button.inline("❌ Cancel download", f"dc:{ctok}")]]
                    if run:
                        prog = float(run.get("progress") or 0)
                        bar = "▰" * int(prog // 10) + "▱" * (10 - int(prog // 10))
                        extra = " · ".join(_esc(x) for x in (run.get("size"), run.get("speed")) if x)
                        count = f" ({len(completed_paths)}/{info['expected']} completed)" if info["expected"] > 1 else ""
                        text = (
                            f"⏬ <b>{_esc(info['title'])}</b>{count}\n{bar} {prog:.0f}%"
                            + (f"  <i>{extra}</i>" if extra else "")
                        )
                    else:
                        text = f"🕐 Queued: <b>{_esc(info['title'])}</b>"
                    if text != info["last_text"]:
                        info["last_text"] = text
                        try:
                            await info["msg"].edit(text, buttons=cancel_btn, parse_mode="html")
                        except MessageNotModifiedError:
                            pass
                        except (RPCError, OSError):
                            pass
                    if time.time() - info["ts"] > state.PENDING_TIMEOUT:
                        state._pending.pop(did, None)
                    continue

                # ── Job no longer active nor queued ──
                if not completed_paths and failed:
                    err = failed[-1].get("error") or failed[-1].get("status") or "unknown error"
                    state._pending.pop(did, None)
                    err_short = err if len(err) <= 300 else err[:300] + "…"
                    try:
                        await info["msg"].edit(
                            f"❌ Download failed: <b>{_esc(info['title'])}</b>\n<code>{_esc(err_short)}</code>",
                            parse_mode="html",
                        )
                    except (RPCError, OSError):
                        pass
                    continue

                # The tracker reports completion before decrypt/remux actually
                # finish: if we still have no ready path, grant a grace window
                # before closing the job anyway.
                if not completed_paths and matches:
                    first_time = "finalizing_since" not in info
                    info.setdefault("finalizing_since", time.time())
                    if first_time:
                        try:
                            await info["msg"].edit(
                                f"📦 Downloaded: <b>{_esc(info['title'])}</b> — <i>processing/converting…</i>",
                                parse_mode="html",
                            )
                        except (RPCError, OSError):
                            pass
                    if time.time() - info["finalizing_since"] <= state.FINALIZE_GRACE_SECONDS:
                        continue

                # Gap between one episode and the next in a season: wait for a
                # quiet period before actually closing the job.
                if (
                    info["expected"] > 1
                    and len(completed_paths) < info["expected"]
                    and (matches or info.get("was_active"))
                    and time.time() - info.get("_last_change", info["ts"]) < state.SETTLE_SECONDS
                ):
                    continue

                # ── Final summary (text only, no file sent) ──
                state._pending.pop(did, None)
                done = len(completed_paths)
                remainder = max(0, info["expected"] - done - len(failed))
                if done >= info["expected"]:
                    where = "\n".join(f"📁 <code>{_esc(p)}</code>" for p in sorted(completed_paths)[:10])
                    suffix = f" ({done} files)" if info["expected"] > 1 else ""
                    text = f"✅ <b>{_esc(info['title'])}</b> — completed{suffix}.\n{where}" if where else f"✅ <b>{_esc(info['title'])}</b> — completed{suffix}."
                else:
                    bits = []
                    if done:
                        bits.append(f"✅ {done} completed")
                    if failed:
                        bits.append(f"❌ {len(failed)} failed")
                    if remainder:
                        bits.append(f"⏭️ {remainder} already present (skipped, not re-downloaded)")
                    detail = "\n".join(bits) or "nothing to report"
                    icon = "✅" if done else ("⚠️" if failed else "ℹ️")
                    text = f"{icon} <b>{_esc(info['title'])}</b>\n{detail}"

                try:
                    await info["msg"].edit(text, parse_mode="html")
                except (RPCError, OSError):
                    pass
            except Exception:  # noqa: BLE001 - a broken job must not stop the monitor for the others
                state.log.exception("Download monitor error %s", did)


__all__ = ['download_monitor_loop']
