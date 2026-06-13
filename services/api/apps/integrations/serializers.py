import re

from rest_framework import serializers

from .models import (
    EmailSettings, MistIntegration, MistSite, NetBoxImport, SMTP_VAULT_PATH,
    UnifiApStatus, UnifiCloudAccount, UnifiConsoleStatus, UnifiController,
)

# NetPulse requires NetBox 4.5+ v2 API tokens. A v2 token is split into a Key ID
# (prefixed ``nbt_``) and a secret value; they're combined as ``{key}.{secret}``
# and sent as a ``Bearer`` credential. Legacy v1 tokens (a single 40-char hex
# string) are no longer supported.
NBT_KEY_PREFIX = "nbt_"
_V1_TOKEN_RE = re.compile(r"^[0-9a-fA-F]{40}$")
NBT_PREFIX_ERROR = (
    "API Key must start with nbt_ (NetBox 4.5+ v2 token format). "
    "Create a new token in NetBox if you only have a legacy token."
)
LEGACY_V1_ERROR = (
    "This looks like a legacy v1 token. spane requires NetBox 4.5+ with "
    "v2 tokens (key starts with nbt_). Please upgrade NetBox or generate "
    "a new v2 token."
)


def _validate_v2_credential(attrs: dict) -> dict:
    """Validate the v2 Key ID + Token and stash the combined ``api_credential``.

    Raises a DRF ``ValidationError`` when the Key ID isn't a v2 token (``nbt_``
    prefix) or the secret is empty; gives a tailored message when the value looks
    like a legacy v1 token.
    """
    key = (attrs.get("api_key") or "").strip()
    token = (attrs.get("api_token") or "").strip()
    if _V1_TOKEN_RE.match(key):
        raise serializers.ValidationError({"api_key": LEGACY_V1_ERROR})
    if not key.startswith(NBT_KEY_PREFIX):
        raise serializers.ValidationError({"api_key": NBT_PREFIX_ERROR})
    if not token:
        raise serializers.ValidationError({"api_token": "API Token is required."})
    attrs["api_credential"] = f"{key}.{token}"
    return attrs


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
    # v2 token: Key ID (nbt_…) + the secret value, validated + combined below.
    api_key = serializers.CharField(write_only=True)
    api_token = serializers.CharField(write_only=True)
    import_options = serializers.DictField(required=False, default=dict)
    verify_ssl = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        return _validate_v2_credential(attrs)


class NetBoxTestRequestSerializer(serializers.Serializer):
    netbox_url = serializers.URLField()
    api_key = serializers.CharField(write_only=True)
    api_token = serializers.CharField(write_only=True)
    verify_ssl = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        return _validate_v2_credential(attrs)


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
        old_host = instance.host
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        # If the controller's mgmt IP was edited, keep its device record in sync.
        if instance.host != old_host:
            from .unifi_sync import update_linked_device_host
            update_linked_device_host(instance)
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
    # Source/vendor so the fleet Wireless page can badge + filter UniFi vs Mist.
    source = serializers.SerializerMethodField()
    vendor = serializers.SerializerMethodField()

    class Meta:
        model = UnifiApStatus
        fields = (
            "device_id", "hostname", "ip_address", "model", "os_version",
            "site_name", "controller_name", "source", "vendor", "state",
            "satisfaction", "client_count", "cpu_pct", "memory_pct",
            "temperature_c", "uptime_seconds", "uplink_speed_mbps", "uplink_type",
            "radios", "last_collected",
        )

    def get_source(self, obj) -> str:
        from .wireless import wireless_source
        return wireless_source((obj.device.platform if obj.device_id else "") or "")

    def get_vendor(self, obj) -> str:
        from .wireless import wireless_vendor
        return wireless_vendor((obj.device.platform if obj.device_id else "") or "")


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


class MistIntegrationSerializer(serializers.ModelSerializer):
    """Singleton Juniper Mist account. The API token is write-only (stored in
    OpenBao); ``api_token_set`` reports whether one is currently stored."""
    api_token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    api_token_set = serializers.SerializerMethodField()

    class Meta:
        model = MistIntegration
        fields = (
            "name", "api_host", "org_id", "org_name", "enabled", "last_sync",
            "last_error", "site_count", "device_count", "api_token", "api_token_set",
        )
        read_only_fields = ("org_id", "org_name", "last_sync", "last_error",
                            "site_count", "device_count")

    def get_api_token_set(self, obj) -> bool:
        from apps.credentials import vault
        from .models import MIST_VAULT_PATH
        try:
            return bool((vault.read_secret(MIST_VAULT_PATH) or {}).get("api_token"))
        except Exception:  # noqa: BLE001
            return False

    def update(self, instance, validated_data):
        api_token = validated_data.pop("api_token", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        # Mirror the secret bundle (api_token + org_id + api_host) into OpenBao.
        # write_mist_secret preserves the stored token when none is supplied, so
        # saving the region/org alone doesn't wipe the token.
        from .mist_client import write_mist_secret
        write_mist_secret(instance, api_token=api_token)
        return instance


class MistSiteSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)

    class Meta:
        model = MistSite
        fields = ("id", "mist_id", "name", "site", "site_name", "address",
                  "country_code", "device_count", "last_sync")
        read_only_fields = fields


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
