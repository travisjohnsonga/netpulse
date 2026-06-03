from rest_framework import serializers

from . import vault
from .models import CredentialProfile

# Write-only secret inputs. Accepted on write, forwarded to OpenBao as one
# object, never persisted to PostgreSQL and never echoed on read.
SECRET_FIELDS = (
    "ssh_password",
    "ssh_private_key",
    "ssh_passphrase",
    "snmpv2c_community",
    "snmpv3_auth_key",
    "snmpv3_priv_key",
    "https_password",
    "https_token",
    "https_api_key",
    "gnmi_password",
    "gnmi_client_cert",
    "gnmi_client_key",
)

# Non-secret model fields the client may set.
CONFIG_FIELDS = (
    "name", "description",
    "ssh_enabled", "ssh_username", "ssh_auth_method", "ssh_port",
    "snmpv2c_enabled", "snmpv2c_port",
    "snmpv3_enabled", "snmpv3_username", "snmpv3_security_level",
    "snmpv3_auth_protocol", "snmpv3_priv_protocol", "snmpv3_port",
    "https_enabled", "https_auth_type", "https_username", "https_port", "https_verify_tls",
    "netconf_enabled", "netconf_port", "netconf_use_ssh_creds", "netconf_username",
    "gnmi_enabled", "gnmi_username", "gnmi_port", "gnmi_tls_enabled",
)


def _secret_field():
    return serializers.CharField(write_only=True, required=False, allow_blank=True)


# RFC 3414: SNMPv3 auth/priv passphrases are at least 8 characters. 64 is a
# sane upper bound (and what NetPulse documents). Validated on write so a
# too-short key is rejected with a clear message instead of silently failing
# auth ("Wrong SNMP PDU digest") at poll time.
SNMPV3_KEY_MIN = 8
SNMPV3_KEY_MAX = 64


class CredentialProfileSerializer(serializers.ModelSerializer):
    device_count = serializers.IntegerField(read_only=True)
    enabled_protocols = serializers.ListField(child=serializers.CharField(), read_only=True)

    # Write-only secrets — not model fields; forwarded to OpenBao.
    ssh_password = _secret_field()
    ssh_private_key = _secret_field()
    ssh_passphrase = _secret_field()
    snmpv2c_community = _secret_field()
    snmpv3_auth_key = _secret_field()
    snmpv3_priv_key = _secret_field()
    https_password = _secret_field()
    https_token = _secret_field()
    https_api_key = _secret_field()
    gnmi_password = _secret_field()
    gnmi_client_cert = _secret_field()
    gnmi_client_key = _secret_field()

    class Meta:
        model = CredentialProfile
        fields = (
            "id", *CONFIG_FIELDS,
            "vault_path", "device_count", "enabled_protocols",
            "created_by", "last_tested", "last_test_result", "last_test_message",
            "created_at", "updated_at",
            *SECRET_FIELDS,
        )
        read_only_fields = (
            "vault_path", "device_count", "enabled_protocols", "created_by",
            "last_tested", "last_test_result", "last_test_message",
            "created_at", "updated_at",
        )

    def validate(self, attrs):
        # A blank secret on update means "leave unchanged", so only length-check
        # a non-blank value the client actually supplied.
        for field in ("snmpv3_auth_key", "snmpv3_priv_key"):
            val = attrs.get(field)
            if val and not (SNMPV3_KEY_MIN <= len(val) <= SNMPV3_KEY_MAX):
                raise serializers.ValidationError({
                    field: f"SNMPv3 passphrase must be {SNMPV3_KEY_MIN}-{SNMPV3_KEY_MAX} characters."
                })
        # Reject placeholder/test-sentinel secrets up front with a clear 400 (the
        # vault layer also refuses these, but that would surface as a 500).
        for field in SECRET_FIELDS:
            if vault.is_placeholder(attrs.get(field)):
                raise serializers.ValidationError({
                    field: "Value looks like a placeholder/test secret; "
                           "use the real credential."
                })
        return attrs

    def _pop_secrets(self, validated_data: dict) -> dict:
        return {f: validated_data.pop(f) for f in SECRET_FIELDS if f in validated_data}

    def create(self, validated_data):
        secrets = self._pop_secrets(validated_data)
        profile = CredentialProfile.objects.create(**validated_data)
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
            # Merge with existing secrets so a partial update doesn't drop others.
            merged = {**vault.read_secret(instance.vault_path), **secrets}
            vault.write_secret(instance.vault_path, merged)
        return instance


class CredentialProfileListSerializer(serializers.ModelSerializer):
    device_count = serializers.IntegerField(read_only=True)
    enabled_protocols = serializers.ListField(child=serializers.CharField(), read_only=True)

    class Meta:
        model = CredentialProfile
        fields = (
            "id", "name", "enabled_protocols", "device_count",
            "last_tested", "last_test_result", "created_at",
        )
