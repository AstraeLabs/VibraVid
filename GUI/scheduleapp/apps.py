# 09.10.26

import logging
import os
import sys

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ScheduleappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "scheduleapp"

    def ready(self) -> None:
        # Same guards as searchapp: only the process that serves requests runs the loop.
        if not any(cmd in sys.argv for cmd in ("runserver", "runserver_plus")):
            return
        if "--noreload" not in sys.argv and os.environ.get("RUN_MAIN") != "true":
            return

        try:
            from .runner import start_schedule_loop

            start_schedule_loop()
        except Exception as exc:
            logger.exception("[Schedule] Failed to start: %s", exc)
