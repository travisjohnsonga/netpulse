from django.contrib import admin

from .models import Collector


@admin.register(Collector)
class CollectorAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "version", "remote_ip", "last_seen_at", "cert_expires_at")
    list_filter = ("status",)
    search_fields = ("name", "remote_ip")
    readonly_fields = ("api_key_hash", "cert_serial", "cert_expires_at", "last_seen_at", "created_at", "updated_at")
