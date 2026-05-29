from django.contrib import admin

from .models import LifecycleMilestone


@admin.register(LifecycleMilestone)
class LifecycleMilestoneAdmin(admin.ModelAdmin):
    list_display = ("device", "milestone_type", "milestone_date", "source")
    list_filter = ("milestone_type",)
    search_fields = ("device__hostname",)
    readonly_fields = ("created_at", "updated_at")
