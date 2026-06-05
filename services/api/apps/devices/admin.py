from django.contrib import admin

from .models import Device, DeviceGroup, DeviceRole, HostnameRule, Site

admin.site.register(Site)
admin.site.register(DeviceGroup)


@admin.register(HostnameRule)
class HostnameRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "pattern", "rule_type", "role", "site", "priority", "enabled")
    list_filter = ("rule_type", "enabled")
    search_fields = ("name", "pattern")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DeviceRole)
class DeviceRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "color", "description", "created_at")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("hostname", "ip_address", "platform", "status", "site", "created_at")
    list_filter = ("status", "platform", "vendor")
    search_fields = ("hostname", "ip_address", "serial_number")
    readonly_fields = ("created_at", "updated_at")
