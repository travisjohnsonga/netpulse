from django.contrib import admin

from .models import NetBoxImport


@admin.register(NetBoxImport)
class NetBoxImportAdmin(admin.ModelAdmin):
    list_display = ("netbox_url", "netbox_version", "status",
                    "sites_imported", "devices_imported", "skipped", "created_at")
    list_filter = ("status",)
    readonly_fields = tuple(f.name for f in NetBoxImport._meta.fields)
