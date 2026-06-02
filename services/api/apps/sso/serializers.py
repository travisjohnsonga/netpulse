from django.urls import NoReverseMatch, reverse
from rest_framework import serializers

from apps.credentials import vault
from .models import SSOProvider


class SSOProviderPublicSerializer(serializers.ModelSerializer):
    """Public view for the login page — no secrets, no config internals."""

    login_url = serializers.SerializerMethodField()

    class Meta:
        model = SSOProvider
        fields = ("id", "name", "provider", "is_default", "login_url")

    def get_login_url(self, obj) -> str:
        try:
            return reverse("social:begin", args=[obj.provider])
        except NoReverseMatch:
            return f"/auth/login/{obj.provider}/"


class SSOProviderAdminSerializer(serializers.ModelSerializer):
    """Admin CRUD. ``client_secret`` is write-only and stored in OpenBao."""

    client_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_secret = serializers.SerializerMethodField()

    class Meta:
        model = SSOProvider
        fields = (
            "id", "name", "provider", "client_id", "client_secret", "has_secret",
            "tenant_id", "okta_domain", "saml_metadata_url",
            "is_enabled", "is_default", "allow_signup", "default_role",
            "allowed_domains", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def get_has_secret(self, obj) -> bool:
        if not obj.vault_path:
            return False
        try:
            return bool((vault.read_secret(obj.vault_path) or {}).get("client_secret"))
        except Exception:  # noqa: BLE001
            return False

    def create(self, validated_data):
        secret = validated_data.pop("client_secret", "")
        provider = SSOProvider.objects.create(**validated_data)
        provider.vault_path = provider.default_vault_path()
        provider.save(update_fields=["vault_path"])
        if secret:
            vault.write_secret(provider.vault_path, {"client_secret": secret})
        return provider

    def update(self, instance, validated_data):
        secret = validated_data.pop("client_secret", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if not instance.vault_path:
            instance.vault_path = instance.default_vault_path()
        instance.save()
        if secret:
            # Merge so a partial update doesn't drop other secret fields.
            merged = {**vault.read_secret(instance.vault_path), "client_secret": secret}
            vault.write_secret(instance.vault_path, merged)
        return instance
