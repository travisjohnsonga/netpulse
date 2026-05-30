from rest_framework import serializers

from apps.credentials import vault

from .models import CVE, CVEFeedSettings, DeviceCVE


class CVESerializer(serializers.ModelSerializer):
    class Meta:
        model = CVE
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class DeviceCVESerializer(serializers.ModelSerializer):
    cve_id = serializers.CharField(source="cve.cve_id", read_only=True)
    severity = serializers.CharField(source="cve.severity", read_only=True)
    cvss_score = serializers.DecimalField(source="cve.cvss_score", max_digits=4, decimal_places=1, read_only=True)

    class Meta:
        model = DeviceCVE
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class CVEFeedSettingsSerializer(serializers.ModelSerializer):
    """
    CVE feed settings. Secrets are write-only and stored in OpenBao; the API
    only ever exposes whether each credential is configured (has_* booleans),
    never the secret or its vault path.
    """

    # Read-only "is configured" flags.
    has_nvd_api_key = serializers.BooleanField(read_only=True)
    has_psirt_credentials = serializers.BooleanField(read_only=True)
    has_paloalto_api_key = serializers.BooleanField(read_only=True)

    # Write-only secret inputs (never echoed back).
    nvd_api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    cisco_psirt_client_id = serializers.CharField(write_only=True, required=False, allow_blank=True)
    cisco_psirt_client_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    paloalto_api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = CVEFeedSettings
        fields = (
            "nvd_enabled", "cisa_kev_enabled", "cisco_psirt_enabled", "paloalto_enabled",
            "has_nvd_api_key", "has_psirt_credentials", "has_paloalto_api_key",
            "nvd_api_key", "cisco_psirt_client_id", "cisco_psirt_client_secret", "paloalto_api_key",
            "created_at", "updated_at",
        )
        read_only_fields = ("created_at", "updated_at")

    def update(self, instance, validated_data):
        nvd_key = validated_data.pop("nvd_api_key", None)
        psirt_id = validated_data.pop("cisco_psirt_client_id", None)
        psirt_secret = validated_data.pop("cisco_psirt_client_secret", None)
        pan_key = validated_data.pop("paloalto_api_key", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        if nvd_key:
            path = instance.nvd_api_key_vault_path or "cve-feeds/nvd"
            vault.write_secret(path, {"nvd_api_key": nvd_key})
            instance.nvd_api_key_vault_path = path

        # PSIRT id + secret are stored together; the block is gated atomically.
        if psirt_id or psirt_secret:
            path = instance.cisco_psirt_client_id_vault_path or "cve-feeds/cisco-psirt"
            merged = {**vault.read_secret(path)}
            if psirt_id:
                merged["client_id"] = psirt_id
            if psirt_secret:
                merged["client_secret"] = psirt_secret
            vault.write_secret(path, merged)
            instance.cisco_psirt_client_id_vault_path = path

        if pan_key:
            path = instance.paloalto_api_key_vault_path or "cve-feeds/paloalto"
            vault.write_secret(path, {"paloalto_api_key": pan_key})
            instance.paloalto_api_key_vault_path = path

        instance.save()
        return instance
