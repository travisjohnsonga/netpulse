from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import LogFilterViewSet, LogQueryView

router = DefaultRouter()
router.register("filters", LogFilterViewSet)

urlpatterns = [
    path("", LogQueryView.as_view(), name="log-query"),
    *router.urls,
]
