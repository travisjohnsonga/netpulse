from django.urls import path

from .wireless import wireless_aps, wireless_summary

urlpatterns = [
    path("summary/", wireless_summary, name="wireless-summary"),
    path("aps/", wireless_aps, name="wireless-aps"),
]
