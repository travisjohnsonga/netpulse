from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import ConfigBackupSettingsView, DeviceConfigViewSet, SyncNowView, TestGitView

router = DefaultRouter()
router.register("configs", DeviceConfigViewSet, basename="deviceconfig")

urlpatterns = [
    path("config-backup/", ConfigBackupSettingsView.as_view(), name="config-backup"),
    path("config-backup/test-git/", TestGitView.as_view(), name="config-backup-test-git"),
    path("config-backup/sync-now/", SyncNowView.as_view(), name="config-backup-sync-now"),
] + router.urls
