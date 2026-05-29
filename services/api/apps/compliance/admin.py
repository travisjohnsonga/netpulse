from django.contrib import admin

from .models import CompliancePolicy, CompliancePolicyRule, ComplianceResult

admin.site.register(CompliancePolicyRule)


@admin.register(CompliancePolicy)
class CompliancePolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)


@admin.register(ComplianceResult)
class ComplianceResultAdmin(admin.ModelAdmin):
    list_display = ("device", "policy", "rule", "outcome", "created_at")
    list_filter = ("outcome",)
    readonly_fields = ("created_at", "updated_at")
