# 06.06.25

import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class SearchappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "searchapp"

    def ready(self) -> None:
        if len(sys.argv) > 1 and sys.argv[1] in {
            "migrate", "makemigrations", "showmigrations", "sqlmigrate", "check", "test",
        }:
            return
        autoreload_active = "runserver" in sys.argv and "--noreload" not in sys.argv
        if autoreload_active and os.environ.get("RUN_MAIN") != "true":
            return

        # Initialize the logger for the application
        try:
            from VibraVid.utils.logger import setup_logger

            setup_logger()
        except Exception as exc:
            logger.exception("[Logger] Failed to initialize: %s", exc)

        # Start the auto loop for the watchlist
        try:
            from .watchlist_auto import start_watchlist_auto_loop

            start_watchlist_auto_loop()
        except Exception as exc:
            logger.exception("[WatchlistAuto] Failed to start: %s", exc)

        # Start the auto loop for ARR (Automatic Release Recognition)
        try:
            from .arr_auto import start_arr_auto_loop

            start_arr_auto_loop()
        except Exception as exc:
            logger.exception("[ARR] Failed to start auto loop: %s", exc)

        # Apply the download concurrency limit on startup
        try:
            from . import views
            from .arr.arr_service import _load_arr_config

            cfg = _load_arr_config()
            views.set_max_download_slots(int(cfg.get("max_concurrent_downloads", 1) or 1))
        except Exception as exc:
            logger.exception("[Downloads] Failed to set max concurrent slots: %s", exc)
