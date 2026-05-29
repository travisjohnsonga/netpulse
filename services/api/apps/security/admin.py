from django.contrib import admin

from .models import DeviceRiskScore


@admin.register(DeviceRiskScore)
class DeviceRiskScoreAdmin(admin.ModelAdmin):
    list_display = ("device", "score", "cve_score", "compliance_score", "lifecycle_score", "last_computed_at")
    ordering = ("-score",)
    readonly_fields = ("last_computed_at", "created_at", "updated_at")
