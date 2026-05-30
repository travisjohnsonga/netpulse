from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import CVEFeedSettingsView, CVEViewSet, DeviceCVEViewSet

router = DefaultRouter()
router.register("cves", CVEViewSet)
router.register("device-cves", DeviceCVEViewSet)

urlpatterns = [
    path("feed-settings/", CVEFeedSettingsView.as_view(), name="cve-feed-settings"),
] + router.urls
