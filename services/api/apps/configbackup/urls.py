from django.urls import path

from .views import ConfigBackupSettingsView, SyncNowView, TestGitView

urlpatterns = [
    path("config-backup/", ConfigBackupSettingsView.as_view(), name="config-backup"),
    path("config-backup/test-git/", TestGitView.as_view(), name="config-backup-test-git"),
    path("config-backup/sync-now/", SyncNowView.as_view(), name="config-backup-sync-now"),
]
