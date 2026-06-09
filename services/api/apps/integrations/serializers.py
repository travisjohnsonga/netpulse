from rest_framework import serializers

from .models import EmailSettings, NetBoxImport, SMTP_VAULT_PATH, UnifiCloudAccount, UnifiController


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


class UnifiControllerSerializer(serializers.ModelSerializer):
    # Write-only: written to OpenBao when supplied; never returned.
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    password_set = serializers.SerializerMethodField()
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = UnifiController
        fields = (
            "id", "name", "host", "port", "username", "verify_ssl", "unifi_site_id",
            "site", "site_name", "enabled", "last_sync", "last_error",
            "device_count", "model", "version", "password", "password_set",
        )
        read_only_fields = ("id", "last_sync", "last_error", "device_count",
                            "model", "version")

    def get_password_set(self, obj) -> bool:
        from apps.credentials import vault
        try:
            return bool((vault.read_secret(obj.vault_path) or {}).get("password"))
        except Exception:  # noqa: BLE001
            return False

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        controller = UnifiController.objects.create(**validated_data)
        if password:
            from apps.credentials import vault
            vault.write_secret(controller.vault_path, {"password": password})
        return controller

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if password:
            from apps.credentials import vault
            vault.write_secret(instance.vault_path, {"password": password})
        return instance


class UnifiCloudAccountSerializer(serializers.ModelSerializer):
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    api_key_set = serializers.SerializerMethodField()

    class Meta:
        model = UnifiCloudAccount
        fields = ("name", "enabled", "last_sync", "last_error", "host_count",
                  "api_key", "api_key_set")
        read_only_fields = ("last_sync", "last_error", "host_count")

    def get_api_key_set(self, obj) -> bool:
        from apps.credentials import vault
        from .models import UNIFI_CLOUD_VAULT_PATH
        try:
            return bool((vault.read_secret(UNIFI_CLOUD_VAULT_PATH) or {}).get("api_key"))
        except Exception:  # noqa: BLE001
            return False

    def update(self, instance, validated_data):
        api_key = validated_data.pop("api_key", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if api_key:
            from apps.credentials import vault
            from .models import UNIFI_CLOUD_VAULT_PATH
            vault.write_secret(UNIFI_CLOUD_VAULT_PATH, {"api_key": api_key})
        return instance
