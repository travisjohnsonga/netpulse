from django.urls import path

from .views import LogQueryView

urlpatterns = [
    path("", LogQueryView.as_view(), name="log-query"),
]
