from django.contrib import admin

from .models import LogFilter


@admin.register(LogFilter)
class LogFilterAdmin(admin.ModelAdmin):
    list_display = ("name", "action", "pattern", "enabled", "created_at")
    list_filter = ("action", "enabled")
    search_fields = ("name", "pattern", "tag")
    readonly_fields = ("created_at",)
