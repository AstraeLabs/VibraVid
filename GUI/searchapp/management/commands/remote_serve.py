# 12.10.26

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from VibraVid.remote.config import load_serve_settings

NO_KEY_ENV = "VIBRAVID_REMOTE_NO_KEY"


class Command(BaseCommand):
    help = "Run the VibraVid remote server (settings from Conf/remote.json -> serve). Refuses to start without API keys."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=None, help="Bind address (default: serve.host, 127.0.0.1)")
        parser.add_argument("--port", type=int, default=None, help="Port (default: serve.port, 8786)")
        parser.add_argument("--no-key", action="store_true", help="Serve without API keys. Local use only: pair it with the default 127.0.0.1 bind")

    def handle(self, *args, **options):
        settings = load_serve_settings()
        host = options["host"] or settings.host
        port = options["port"] or settings.port

        if not settings.users and not options["no_key"]:
            raise CommandError("No API keys in Conf/remote.json -> serve.users. Add one, or pass --no-key for a local-only server.")

        if options["no_key"]:
            if host not in ("127.0.0.1", "localhost", "::1"):
                raise CommandError(f"--no-key is only allowed on a loopback address, not '{host}'.")
            os.environ[NO_KEY_ENV] = "1"
            self.stdout.write(self.style.WARNING("Running WITHOUT API keys (loopback only)."))

        self.stdout.write(f"VibraVid remote server on http://{host}:{port}/api/remote/")
        call_command("runserver", f"{host}:{port}", use_reloader=False)
