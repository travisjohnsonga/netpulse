from django.contrib import admin

from .models import ConfigBackupSettings, DeviceConfig


@admin.register(ConfigBackupSettings)
class ConfigBackupSettingsAdmin(admin.ModelAdmin):
    list_display = ("local_enabled", "git_enabled", "git_provider", "git_sync_frequency", "last_sync_at")
    readonly_fields = ("git_vault_path", "last_sync_at", "last_sync_success", "last_commit_sha")


@admin.register(DeviceConfig)
class DeviceConfigAdmin(admin.ModelAdmin):
    list_display = ("device", "config_type", "collected_at", "collected_by",
                    "changed_from_previous", "git_commit_sha")
    list_filter = ("config_type", "collected_by", "changed_from_previous")
    search_fields = ("device__hostname", "content_hash")
    readonly_fields = ("created_at", "updated_at")
