# 09.10.26

from django.urls import path

from . import views

urlpatterns = [
    path("add/", views.schedule_add, name="schedule_add"),
    path("list/", views.schedule_list, name="schedule_list"),
    path("update/", views.schedule_update, name="schedule_update"),
]
