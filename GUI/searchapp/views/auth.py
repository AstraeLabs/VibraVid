# 01.11.26

from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from .. import security


def _client_address(request) -> str:
    return request.META.get("REMOTE_ADDR", "")


def _safe_next(request, value: str) -> str:
    if value and url_has_allowed_host_and_scheme(value, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return value
    return "/"


@require_http_methods(["GET", "POST"])
def login_view(request):
    next_url = _safe_next(request, request.POST.get("next") or request.GET.get("next") or "")
    if not security.gui_auth_enabled() or request.session.get(security.SESSION_KEY):
        return redirect(next_url)

    error = ""
    status = 200
    if request.method == "POST":
        address = _client_address(request)
        if security.login_throttle.is_blocked(address):
            error = "Too many failed attempts. Try again in a few minutes."
            status = 429
        elif security.check_credentials(request.POST.get("username", ""), request.POST.get("password", "")):
            security.login_throttle.reset(address)
            request.session.cycle_key()
            request.session[security.SESSION_KEY] = True
            return redirect(next_url)
        else:
            security.login_throttle.record_failure(address)
            error = "Wrong username or password."
            status = 401
    return render(request, "searchapp/cinema_login.html", {"error": error, "next": next_url}, status=status)


@require_http_methods(["POST"])
def logout_view(request):
    request.session.flush()
    return redirect("/login/" if security.gui_auth_enabled() else "/")
