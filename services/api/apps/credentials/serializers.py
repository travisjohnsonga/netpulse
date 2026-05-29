from rest_framework import serializers

from .models import CredentialProfile, DeviceCredential
from . import vault

# Secret fields accepted on write. They are NEVER stored on the model or
# returned on read — they are pushed straight to OpenBao and discarded here.
SECRET_FIELDS = (
    "community",       # SNMP v1/v2c
    "auth_password",   # SNMP v3 auth
    "priv_password",   # SNMP v3 priv
    "password",        # SSH / HTTP basic / NETCONF / gNMI
    "private_key",     # SSH key
    "passphrase",      # SSH key passphrase
    "token",           # HTTP bearer token
    "api_key",         # HTTP API key
)


class CredentialProfileSerializer(serializers.ModelSerializer):
    device_count = serializers.IntegerField(read_only=True)

    # Write-only secret inputs — accepted, forwarded to OpenBao, never echoed.
    community = serializers.CharField(write_only=True, required=False, allow_blank=True)
    auth_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    priv_password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    private_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    passphrase = serializers.CharField(write_only=True, required=False, allow_blank=True)
    token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = CredentialProfile
        fields = (
            "id", "name", "credential_type", "description",
            "username", "auth_method", "port", "tls_enabled",
            "snmp_version", "snmp_security_level", "auth_protocol", "priv_protocol",
            "vault_path", "device_count",
            "created_by", "last_tested", "last_test_result", "last_test_message",
            "created_at", "updated_at",
            # write-only secrets
            *SECRET_FIELDS,
        )
        read_only_fields = (
            "vault_path", "device_count", "created_by",
            "last_tested", "last_test_result", "last_test_message",
            "created_at", "updated_at",
        )

    def _pop_secrets(self, validated_data: dict) -> dict:
        return {f: validated_data.pop(f) for f in SECRET_FIELDS if f in validated_data}

    def create(self, validated_data):
        secrets = self._pop_secrets(validated_data)
        profile = CredentialProfile.objects.create(**validated_data)
        # vault_path derives from the pk, so set it after the row exists.
        profile.vault_path = profile.default_vault_path()
        profile.save(update_fields=["vault_path"])
        vault.write_secret(profile.vault_path, secrets)
        return profile

    def update(self, instance, validated_data):
        secrets = self._pop_secrets(validated_data)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if not instance.vault_path:
            instance.vault_path = instance.default_vault_path()
        instance.save()
        if secrets:
            vault.write_secret(instance.vault_path, secrets)
        return instance


class CredentialProfileListSerializer(serializers.ModelSerializer):
    device_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = CredentialProfile
        fields = (
            "id", "name", "credential_type", "username", "device_count",
            "last_tested", "last_test_result", "created_at",
        )


class DeviceCredentialSerializer(serializers.ModelSerializer):
    credential_name = serializers.CharField(source="credential.name", read_only=True)
    credential_type = serializers.CharField(source="credential.credential_type", read_only=True)
    device_hostname = serializers.CharField(source="device.hostname", read_only=True)

    class Meta:
        model = DeviceCredential
        fields = (
            "id", "device", "device_hostname",
            "credential", "credential_name", "credential_type",
            "purpose", "is_primary", "last_used", "last_success",
            "failure_count", "notes", "created_at", "updated_at",
        )
        # device is taken from the URL on the nested create endpoint.
        read_only_fields = ("device", "last_used", "last_success", "failure_count",
                            "created_at", "updated_at")
