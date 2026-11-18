# 07.05.26

import logging
import sys
import threading
import time

logger = logging.getLogger("ARR")

_loop_started = False
_loop_lock = threading.Lock()


def _should_start_loop() -> bool:
    return any(cmd in sys.argv for cmd in ("runserver", "runserver_plus"))


def _arr_loop() -> None:
    """Main background loop for ARR polling."""
    from .arr.arr_service import _load_arr_config, _reconcile_orphaned_downloads, trigger_polling_sync

    # Any queue row left in 'Downloading' at startup is orphaned by a previous
    # crash/restart (nothing is running yet), so recover them unconditionally.
    try:
        from django.db import close_old_connections

        close_old_connections()
        _reconcile_orphaned_downloads()
    except Exception as exc:
        logger.error(f"Startup orphaned-download reconcile failed: {exc}")

    last_poll = 0.0
    last_resync = 0.0

    while True:
        try:
            cfg = _load_arr_config()

            if not cfg.get("enabled"):
                time.sleep(30)
                continue

            if not cfg.get("enable_polling"):
                time.sleep(30)
                continue

            now = time.time()
            poll_interval = cfg.get("polling_interval", 300)
            resync_interval = cfg.get("full_resync_interval", 21600)

            # Full reconciliation sync
            if now - last_resync >= resync_interval:
                logger.info("Starting full ARR reconciliation sync")
                try:
                    count = trigger_polling_sync(full_resync=True)
                    logger.info(f"Reconciliation complete: {count} items enqueued")
                except Exception as exc:
                    logger.error(f"Reconciliation sync error: {exc}")
                last_resync = now
                last_poll = now  # Also counts as a poll

            # Incremental polling
            elif now - last_poll >= poll_interval:
                logger.info("📡 Starting incremental ARR poll")
                try:
                    count = trigger_polling_sync(full_resync=False)
                    logger.info(f"📡 Incremental poll complete: {count} items enqueued")
                except Exception as exc:
                    logger.error(f"Incremental poll error: {exc}")
                last_poll = now

            # Sleep a short interval and re-check
            time.sleep(min(30, poll_interval))

        except Exception as exc:
            logger.error(f"ARR loop error: {exc}")
            time.sleep(60)


def start_arr_auto_loop() -> None:
    """Start the ARR polling background thread (called from AppConfig.ready)."""
    global _loop_started

    if not _should_start_loop():
        return

    with _loop_lock:
        if _loop_started:
            logger.info("[ARR] Loop already running, skipping duplicate start")
            return
        _loop_started = True

    from .arr.arr_service import _load_arr_config

    cfg = _load_arr_config()
    if not cfg.get("enabled"):
        logger.info("[ARR] ARR services disabled, not starting loop")
        _loop_started = False
        return

    thread = threading.Thread(
        target=_arr_loop,
        daemon=True,
        name="ArrAutoLoop",
    )
    thread.start()
    logger.info("[ARR] Background polling loop started")
