from django.contrib import admin

from .models import (
    CompliancePolicy,
    CompliancePolicyRule,
    ComplianceResult,
    ComplianceTemplate,
    ComplianceTemplateResult,
    DeviceComplianceOverride,
)

admin.site.register(CompliancePolicyRule)
admin.site.register(DeviceComplianceOverride)


@admin.register(ComplianceTemplate)
class ComplianceTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "platform", "role", "site", "enabled", "updated_at")
    list_filter = ("enabled", "platform")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ComplianceTemplateResult)
class ComplianceTemplateResultAdmin(admin.ModelAdmin):
    list_display = ("device", "template", "status", "score", "checked_at")
    list_filter = ("status",)
    readonly_fields = ("checked_at",)


@admin.register(CompliancePolicy)
class CompliancePolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(ComplianceResult)
class ComplianceResultAdmin(admin.ModelAdmin):
    list_display = ("device", "policy", "rule", "outcome", "created_at")
    list_filter = ("outcome",)
    readonly_fields = ("created_at", "updated_at")
