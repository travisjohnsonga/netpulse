from rest_framework import serializers

from .models import EmailSettings, NetBoxImport, SMTP_VAULT_PATH


class NetBoxImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = NetBoxImport
        fields = (
            "id", "netbox_url", "netbox_version", "status", "options",
            "sites_imported", "devices_imported", "devices_updated", "skipped", "errors",
            "started_at", "finished_at", "created_at",
        )
        read_only_fields = fields


class NetBoxImportRequestSerializer(serializers.Serializer):
    netbox_url = serializers.URLField()
    api_token = serializers.CharField(write_only=True)
    import_options = serializers.DictField(required=False, default=dict)


class NetBoxTestRequestSerializer(serializers.Serializer):
    netbox_url = serializers.URLField()
    api_token = serializers.CharField(write_only=True)


class NetBoxTestResponseSerializer(serializers.Serializer):
    ok = serializers.BooleanField()
    version = serializers.CharField(allow_blank=True)
    message = serializers.CharField()


class EmailSettingsSerializer(serializers.ModelSerializer):
    # Write-only: a non-blank value is written to OpenBao; never returned.
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    # Read-only: whether a password is currently stored.
    password_set = serializers.SerializerMethodField()

    class Meta:
        model = EmailSettings
        fields = (
            "provider", "host", "port", "username", "use_tls", "use_ssl",
            "from_email", "from_name", "enabled", "password", "password_set",
        )

    def get_password_set(self, obj) -> bool:
        from apps.credentials import vault
        try:
            return bool((vault.read_secret(SMTP_VAULT_PATH) or {}).get("password"))
        except Exception:  # noqa: BLE001
            return False

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        # Only touch OpenBao when a non-blank password is supplied, so saving
        # other settings doesn't wipe the stored secret.
        if password:
            from apps.credentials import vault
            vault.write_secret(SMTP_VAULT_PATH, {"password": password})
        return instance
