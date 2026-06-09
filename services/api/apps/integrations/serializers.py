from rest_framework import serializers

from .models import (
    EmailSettings, NetBoxImport, SMTP_VAULT_PATH, UnifiApStatus,
    UnifiCloudAccount, UnifiConsoleStatus, UnifiController,
)


class NetBoxImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = NetBoxImport
        fields = (
            "id", "netbox_url", "netbox_version", "status", "options", "verify_ssl",
            "sites_imported", "devices_imported", "devices_updated", "skipped", "errors",
            "started_at", "finished_at", "created_at",
        )
        read_only_fields = fields


class NetBoxImportRequestSerializer(serializers.Serializer):
    netbox_url = serializers.URLField()
    api_token = serializers.CharField(write_only=True)
    import_options = serializers.DictField(required=False, default=dict)
    verify_ssl = serializers.BooleanField(required=False, default=True)


class NetBoxTestRequestSerializer(serializers.Serializer):
    netbox_url = serializers.URLField()
    api_token = serializers.CharField(write_only=True)
    verify_ssl = serializers.BooleanField(required=False, default=True)


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
    # Credentials come from a CredentialProfile (HTTPS/SSH); no password here.
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    credential_profile_name = serializers.CharField(
        source="credential_profile.name", read_only=True, default=None)

    class Meta:
        model = UnifiController
        fields = (
            "id", "name", "host", "port", "verify_ssl", "unifi_site_id",
            "site", "site_name", "credential_profile", "credential_profile_name",
            "enabled", "last_sync", "last_error", "device_count", "model", "version",
        )
        read_only_fields = ("id", "last_sync", "last_error", "device_count",
                            "model", "version")

    def create(self, validated_data):
        return UnifiController.objects.create(**validated_data)

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class UnifiApStatusSerializer(serializers.ModelSerializer):
    """Latest AP snapshot joined with the owning Device's identity fields, for
    the device-detail Wireless tab and the fleet Wireless page."""
    device_id = serializers.IntegerField(source="device.id", read_only=True)
    hostname = serializers.CharField(source="device.hostname", read_only=True)
    ip_address = serializers.CharField(source="device.ip_address", read_only=True)
    model = serializers.CharField(source="device.model", read_only=True)
    os_version = serializers.CharField(source="device.os_version", read_only=True)
    site_name = serializers.CharField(source="device.site.name", read_only=True, default=None)
    controller_name = serializers.CharField(source="controller.name", read_only=True, default=None)

    class Meta:
        model = UnifiApStatus
        fields = (
            "device_id", "hostname", "ip_address", "model", "os_version",
            "site_name", "controller_name", "state", "satisfaction",
            "client_count", "cpu_pct", "memory_pct", "temperature_c",
            "uptime_seconds", "uplink_speed_mbps", "uplink_type", "radios",
            "last_collected",
        )


class UnifiConsoleStatusSerializer(serializers.ModelSerializer):
    """Latest console/gateway snapshot for the device-detail Overview panels."""
    device_id = serializers.IntegerField(source="device.id", read_only=True)
    hostname = serializers.CharField(source="device.hostname", read_only=True)
    model = serializers.CharField(source="device.model", read_only=True)
    os_version = serializers.CharField(source="device.os_version", read_only=True)
    controller_name = serializers.CharField(source="controller.name", read_only=True, default=None)

    class Meta:
        model = UnifiConsoleStatus
        fields = (
            "device_id", "hostname", "model", "os_version", "controller_name",
            "state", "satisfaction", "cpu_pct", "memory_pct", "temperature_c",
            "uptime_seconds", "loadavg_1", "loadavg_5", "loadavg_15",
            "num_adopted", "num_disconnected", "num_pending", "wans", "last_collected",
        )


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
