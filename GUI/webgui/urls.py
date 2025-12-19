# 06-06-2025 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("searchapp.urls")),
]