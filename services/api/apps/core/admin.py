from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import NetPulseUser


@admin.register(NetPulseUser)
class NetPulseUserAdmin(UserAdmin):
    list_display  = ("username", "email", "role", "is_staff", "is_active")
    list_filter   = ("role", "is_staff", "is_active")
    search_fields = ("username", "email")
    fieldsets     = UserAdmin.fieldsets + (
        ("spane", {"fields": ("role",)}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("spane", {"fields": ("role",)}),
    )
