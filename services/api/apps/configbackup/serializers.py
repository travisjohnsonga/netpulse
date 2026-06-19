from rest_framework import serializers

from .models import ConfigBackupSettings, ConfigCollectionLog, DeviceConfig


class ConfigBackupSettingsSerializer(serializers.ModelSerializer):
    # Write-only git credential (token / SSH key) — forwarded to OpenBao.
    git_credential = serializers.CharField(write_only=True, required=False, allow_blank=True)
    # Approximate local usage from stored config content.
    local_used_bytes = serializers.SerializerMethodField()

    class Meta:
        model = ConfigBackupSettings
        fields = (
            "local_enabled", "local_path", "local_retention_days",
            "git_enabled", "git_provider", "git_repo_url", "git_branch",
            "git_auth_method", "git_vault_path", "git_commit_author",
            "git_commit_email", "git_sync_frequency",
            "last_sync_at", "last_sync_success", "last_commit_sha",
            "local_used_bytes", "git_credential", "updated_at",
        )
        read_only_fields = (
            "git_vault_path", "last_sync_at", "last_sync_success",
            "last_commit_sha", "local_used_bytes", "updated_at",
        )

    def get_local_used_bytes(self, obj) -> int:
        # Cheap estimate — sum of stored config sizes.
        return sum(len(c) for c in DeviceConfig.objects.values_list("content", flat=True))


class TestGitRequestSerializer(serializers.Serializer):
    git_repo_url = serializers.CharField(required=False, allow_blank=True)


class ConfigDiffRequestSerializer(serializers.Serializer):
    """Compare two stored snapshots (by id) or two raw config strings."""
    left = serializers.IntegerField(required=False, help_text="DeviceConfig id (the 'old' side).")
    right = serializers.IntegerField(required=False, help_text="DeviceConfig id (the 'new' side).")
    old = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    new = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    context = serializers.IntegerField(required=False, default=3, min_value=0, max_value=100)


class ConfigDiffLineSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["context", "add", "remove"])
    content = serializers.CharField(allow_blank=True)
    line_no = serializers.IntegerField()


class ConfigDiffHunkSerializer(serializers.Serializer):
    old_start = serializers.IntegerField()
    old_count = serializers.IntegerField()
    new_start = serializers.IntegerField()
    new_count = serializers.IntegerField()
    lines = ConfigDiffLineSerializer(many=True)


class ConfigDiffSummarySerializer(serializers.Serializer):
    added = serializers.IntegerField()
    removed = serializers.IntegerField()
    changed = serializers.IntegerField()


class ConfigDiffResponseSerializer(serializers.Serializer):
    summary = ConfigDiffSummarySerializer()
    hunks = ConfigDiffHunkSerializer(many=True)


class SimpleResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    message = serializers.CharField()
    last_commit_sha = serializers.CharField(required=False, allow_blank=True)


class DeviceConfigSerializer(serializers.ModelSerializer):
    # Human-readable CLI for display. For AOS-CX backups stored as REST JSON this
    # renders the JSON to CLI on the fly; for everything else (already CLI, incl.
    # new AOS-CX backups stored as CLI) it equals `content`.
    rendered_content = serializers.SerializerMethodField()

    class Meta:
        model = DeviceConfig
        fields = [
            "id", "device", "config_type", "collected_at", "collected_by",
            "content", "rendered_content", "content_hash", "changed_from_previous",
            "diff_summary", "git_commit_sha", "compliance_status",
            "startup_match", "startup_checked_at",
        ]

    def get_rendered_content(self, obj):
        from apps.devices.aos_cx_render import render_config_content
        platform = obj.device.platform if obj.device_id else ""
        return render_config_content(obj.content or "", platform)


class ConfigCollectionLogSerializer(serializers.ModelSerializer):
    device_hostname = serializers.CharField(source="device.hostname", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ConfigCollectionLog
        fields = [
            "id", "device", "device_hostname", "collected_at", "status",
            "status_label", "collected_by", "duration_ms", "error_message",
            "config_changed", "bytes_collected", "method",
        ]
