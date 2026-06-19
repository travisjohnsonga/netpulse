from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    BackupConfigView,
    BackupDownloadView,
    BackupRecordViewSet,
    BackupRunView,
    BackupTestConnectionView,
)

router = DefaultRouter()
router.register("records", BackupRecordViewSet, basename="backup-record")

urlpatterns = [
    path("config/", BackupConfigView.as_view(), name="backup-config"),
    path("run/", BackupRunView.as_view(), name="backup-run"),
    path("test-connection/", BackupTestConnectionView.as_view(), name="backup-test-connection"),
    path("download/<int:pk>/", BackupDownloadView.as_view(), name="backup-download"),
    *router.urls,
]
