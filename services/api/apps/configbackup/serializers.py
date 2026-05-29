from rest_framework import serializers

from .models import ConfigBackupSettings, DeviceConfig


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


class SimpleResultSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    message = serializers.CharField()
    last_commit_sha = serializers.CharField(required=False, allow_blank=True)


class DeviceConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceConfig
        fields = [
            "id", "device", "config_type", "collected_at", "collected_by",
            "content", "content_hash", "changed_from_previous", "diff_summary",
            "git_commit_sha", "compliance_status",
        ]
