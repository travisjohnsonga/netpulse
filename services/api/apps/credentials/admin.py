from django.contrib import admin

from .models import CredentialProfile, DeviceCredential


class DeviceCredentialInline(admin.TabularInline):
    model = DeviceCredential
    extra = 0
    autocomplete_fields = ("device",)
    readonly_fields = ("last_used", "last_success", "failure_count")


@admin.register(CredentialProfile)
class CredentialProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "credential_type", "username", "device_count",
                    "last_test_result", "last_tested", "created_at")
    list_filter = ("credential_type", "last_test_result")
    search_fields = ("name", "username", "description")
    # vault_path and audit fields are managed by the API, never edited by hand.
    readonly_fields = ("vault_path", "last_tested", "last_test_result",
                       "last_test_message", "created_at", "updated_at")
    inlines = [DeviceCredentialInline]


@admin.register(DeviceCredential)
class DeviceCredentialAdmin(admin.ModelAdmin):
    list_display = ("device", "credential", "purpose", "is_primary",
                    "last_success", "failure_count")
    list_filter = ("purpose", "is_primary")
    search_fields = ("device__hostname", "credential__name")
    autocomplete_fields = ("device", "credential")
    readonly_fields = ("last_used", "last_success", "failure_count",
                       "created_at", "updated_at")
