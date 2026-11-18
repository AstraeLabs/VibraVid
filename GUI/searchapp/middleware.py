# 01.11.26

from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.http import urlencode

from . import security


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not security.gui_auth_enabled():
            return self.get_response(request)

        path = request.path
        if path.startswith(security.EXEMPT_PREFIXES) or request.session.get(security.SESSION_KEY):
            return self.get_response(request)
        if path.startswith(security.BOT_TOKEN_PATHS) and security.bot_token_valid(request):
            return self.get_response(request)

        wants_json = path.startswith("/api/") or request.headers.get("X-Requested-With") == "XMLHttpRequest" \
            or "application/json" in request.headers.get("Accept", "")
        if wants_json:
            return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
        query = urlencode({"next": request.get_full_path()}) if request.method == "GET" and path != "/" else ""
        return redirect(f"/login/?{query}" if query else "/login/")
