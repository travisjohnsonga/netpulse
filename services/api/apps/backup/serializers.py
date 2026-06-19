"""
Serializers for the backup subsystem.

The config serializer exposes ONLY non-secret fields plus boolean ``*_set``
presence flags computed from OpenBao (mirrors apps.integrations EmailSettings /
MistIntegration: write-only secrets + ``*_set`` booleans). Secret *values* are
write-only and are persisted to OpenBao on update — they never appear in a
response. The record serializer is read-only.
"""
from __future__ import annotations

from rest_framework import serializers

from apps.credentials import vault

from .models import (
    ENCRYPTION_VAULT_PATH,
    GIT_VAULT_PATH,
    S3_VAULT_PATH,
    SCP_VAULT_PATH,
    BackupConfig,
    BackupRecord,
)


def _has(path: str, key: str) -> bool:
    try:
        return bool((vault.read_secret(path) or {}).get(key))
    except Exception:  # noqa: BLE001
        return False


class BackupConfigSerializer(serializers.ModelSerializer):
    # Write-only destination secrets — stored in OpenBao, never returned.
    scp_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    scp_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    git_ssh_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    s3_access_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    s3_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    # The scheduled-backup encryption password (used by the scheduler only).
    encryption_password = serializers.CharField(write_only=True, required=False, allow_blank=True)

    # Read-only presence flags computed from OpenBao.
    scp_password_set = serializers.SerializerMethodField()
    scp_key_set = serializers.SerializerMethodField()
    git_ssh_key_set = serializers.SerializerMethodField()
    s3_access_key_set = serializers.SerializerMethodField()
    s3_secret_set = serializers.SerializerMethodField()
    encryption_password_set = serializers.SerializerMethodField()

    class Meta:
        model = BackupConfig
        fields = (
            "schedule", "schedule_time", "schedule_day", "retention_days",
            "include_postgres", "include_influxdb", "include_openbao",
            "include_config_files", "include_ssl_certs", "include_influxdb_days",
            "local_path", "destination",
            "scp_host", "scp_port", "scp_username", "scp_path",
            "git_repo_url", "git_branch", "git_path",
            "s3_bucket", "s3_prefix", "s3_endpoint", "s3_region",
            "encryption_required",
            # write-only secrets
            "scp_password", "scp_key", "git_ssh_key", "s3_access_key", "s3_secret",
            "encryption_password",
            # read-only presence flags
            "scp_password_set", "scp_key_set", "git_ssh_key_set",
            "s3_access_key_set", "s3_secret_set", "encryption_password_set",
            "updated_at",
        )
        read_only_fields = ("updated_at",)

    def get_scp_password_set(self, obj) -> bool:
        return _has(SCP_VAULT_PATH, "password")

    def get_scp_key_set(self, obj) -> bool:
        return _has(SCP_VAULT_PATH, "ssh_key")

    def get_git_ssh_key_set(self, obj) -> bool:
        return _has(GIT_VAULT_PATH, "ssh_key")

    def get_s3_access_key_set(self, obj) -> bool:
        return _has(S3_VAULT_PATH, "access_key")

    def get_s3_secret_set(self, obj) -> bool:
        return _has(S3_VAULT_PATH, "secret_key")

    def get_encryption_password_set(self, obj) -> bool:
        return _has(ENCRYPTION_VAULT_PATH, "password")

    def update(self, instance, validated_data):
        # Pull secrets out before persisting the (secret-free) model row.
        scp_password = validated_data.pop("scp_password", None)
        scp_key = validated_data.pop("scp_key", None)
        git_ssh_key = validated_data.pop("git_ssh_key", None)
        s3_access_key = validated_data.pop("s3_access_key", None)
        s3_secret = validated_data.pop("s3_secret", None)
        encryption_password = validated_data.pop("encryption_password", None)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        # Only touch OpenBao when a non-blank value is supplied, so saving other
        # settings never wipes an already-stored secret. write_secret discards
        # empty values, so partial bundles are fine.
        scp_bundle = {}
        if scp_password:
            scp_bundle["password"] = scp_password
        if scp_key:
            scp_bundle["ssh_key"] = scp_key
        if scp_bundle:
            vault.write_secret(SCP_VAULT_PATH, scp_bundle)
        if git_ssh_key:
            vault.write_secret(GIT_VAULT_PATH, {"ssh_key": git_ssh_key})
        s3_bundle = {}
        if s3_access_key:
            s3_bundle["access_key"] = s3_access_key
        if s3_secret:
            s3_bundle["secret_key"] = s3_secret
        if s3_bundle:
            vault.write_secret(S3_VAULT_PATH, s3_bundle)
        if encryption_password:
            vault.write_secret(ENCRYPTION_VAULT_PATH, {"password": encryption_password})
        return instance


class BackupRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupRecord
        fields = (
            "id", "started_at", "completed_at", "status", "triggered_by",
            "components", "filename", "file_size_bytes", "local_path",
            "remote_path", "error_message", "duration_seconds",
            "encrypted", "encryption_hint",
        )
        read_only_fields = fields
