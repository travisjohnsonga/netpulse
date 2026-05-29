from django.contrib import admin

from .models import CredentialProfile


@admin.register(CredentialProfile)
class CredentialProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled_protocols", "device_count",
                    "last_test_result", "last_tested", "created_at")
    list_filter = ("ssh_enabled", "snmpv2c_enabled", "snmpv3_enabled",
                   "https_enabled", "netconf_enabled", "gnmi_enabled", "last_test_result")
    search_fields = ("name", "description")
    # vault_path and audit fields are managed by the API, never edited by hand.
    readonly_fields = ("vault_path", "last_tested", "last_test_result",
                       "last_test_message", "created_at", "updated_at")

    @admin.display(description="Protocols")
    def enabled_protocols(self, obj):
        return ", ".join(obj.enabled_protocols) or "—"
