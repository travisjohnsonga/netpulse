from django.contrib import admin

from .models import AlertChannel, AlertEvent, AlertRule

admin.site.register(AlertChannel)


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "severity", "is_active", "cooldown_minutes")
    list_filter = ("severity", "is_active")


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ("rule", "state", "created_at", "resolved_at")
    list_filter = ("state", "rule__severity")
    readonly_fields = ("created_at", "updated_at")
