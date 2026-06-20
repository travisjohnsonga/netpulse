from django.contrib import admin

from .models import ChatOpsChannel, ChatOpsConfig, ChatOpsIdentity, ChatOpsPlatform


@admin.register(ChatOpsPlatform)
class ChatOpsPlatformAdmin(admin.ModelAdmin):
    list_display = ("platform", "enabled", "display_name", "updated_at")
    list_filter = ("enabled",)


@admin.register(ChatOpsChannel)
class ChatOpsChannelAdmin(admin.ModelAdmin):
    list_display = ("platform", "channel_id", "name", "purpose", "enabled")
    list_filter = ("platform", "purpose", "enabled")


@admin.register(ChatOpsIdentity)
class ChatOpsIdentityAdmin(admin.ModelAdmin):
    list_display = ("platform", "platform_user_name", "platform_user_id", "user")
    list_filter = ("platform",)


@admin.register(ChatOpsConfig)
class ChatOpsConfigAdmin(admin.ModelAdmin):
    list_display = ("allow_unmapped_read", "require_approved_channel", "updated_at")
