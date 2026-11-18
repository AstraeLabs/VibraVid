# 06.06.25

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")
application = get_asgi_application()
