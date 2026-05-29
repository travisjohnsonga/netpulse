from django.contrib import admin

from .models import Device, DeviceGroup, Site

admin.site.register(Site)
admin.site.register(DeviceGroup)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("hostname", "ip_address", "platform", "status", "site", "created_at")
    list_filter = ("status", "platform", "vendor")
    search_fields = ("hostname", "ip_address", "serial_number")
    readonly_fields = ("created_at", "updated_at")
