from django.contrib import admin

from .models import CVE, DeviceCVE


@admin.register(CVE)
class CVEAdmin(admin.ModelAdmin):
    list_display = ("cve_id", "severity", "cvss_score", "published_at")
    list_filter = ("severity",)
    search_fields = ("cve_id", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DeviceCVE)
class DeviceCVEAdmin(admin.ModelAdmin):
    list_display = ("device", "cve", "is_patched", "patched_at")
    list_filter = ("is_patched", "cve__severity")
