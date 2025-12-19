# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "webgui.settings")
application = get_asgi_application()