# 06.06.25

from django.contrib import admin
from django.contrib.staticfiles.views import serve as _static_serve
from django.urls import include, path, re_path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("searchapp.urls")),
    path("schedule/", include("scheduleapp.urls")),
]

urlpatterns += [
    re_path(
        r"^static/(?P<path>.*)$",
        lambda request, path: _static_serve(request, path, insecure=True),
    ),
]
