"""
ChatOps DRF serializers.

Secret fields are **write-only**: a non-blank value is written to OpenBao and is
never returned. Serialized output exposes only a per-field "stored?" indicator
showing the ``SECRET_PLACEHOLDER`` text — never the value (Security Rules 3 + 4).
"""
from __future__ import annotations

from rest_framework import serializers

from .models import (
    ChatOpsChannel, ChatOpsConfig, ChatOpsIdentity, ChatOpsPlatform,
    PLATFORM_SECRET_FIELDS, read_chatops_secrets, write_chatops_secrets,
)

SECRET_PLACEHOLDER = "🔒 Stored securely in OpenBao"

# Union of every platform's secret field names — exposed as optional write-only
# inputs; only the keys belonging to the row's platform are actually persisted.
_ALL_SECRET_KEYS = sorted({k for keys in PLATFORM_SECRET_FIELDS.values() for k in keys})


class ChatOpsPlatformSerializer(serializers.ModelSerializer):
    # Per-platform write-only secret inputs (stored in OpenBao, never returned).
    signing_secret = serializers.CharField(write_only=True, required=False, allow_blank=True)
    bot_token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    public_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    # Read-only: which secret fields this platform uses + whether each is stored.
    secret_fields = serializers.SerializerMethodField()
    secrets = serializers.SerializerMethodField()

    class Meta:
        model = ChatOpsPlatform
        fields = (
            "platform", "enabled", "display_name", "default_response_channel",
            "secret_fields", "secrets",
            # write-only secret inputs
            "signing_secret", "bot_token", "public_key", "token",
            "created_at", "updated_at",
        )
        read_only_fields = ("platform", "created_at", "updated_at")

    def get_secret_fields(self, obj) -> list[str]:
        return list(PLATFORM_SECRET_FIELDS.get(obj.platform, ()))

    def get_secrets(self, obj) -> dict:
        """Map each of the platform's secret fields → placeholder (if stored) or ''.
        Never returns the actual secret value."""
        stored = read_chatops_secrets(obj.platform)
        return {
            field: (SECRET_PLACEHOLDER if stored.get(field) else "")
            for field in PLATFORM_SECRET_FIELDS.get(obj.platform, ())
        }

    def update(self, instance, validated_data):
        # Pull out any provided secret inputs before saving model fields.
        provided = {k: validated_data.pop(k, None) for k in _ALL_SECRET_KEYS}
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        # Persist only the secrets relevant to THIS platform, and only non-blank
        # values (so saving settings alone never wipes a stored secret).
        relevant = PLATFORM_SECRET_FIELDS.get(instance.platform, ())
        to_write = {k: v for k, v in provided.items() if k in relevant and v}
        if to_write:
            write_chatops_secrets(instance.platform, to_write)
        return instance


class ChatOpsChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatOpsChannel
        fields = (
            "id", "platform", "channel_id", "name", "purpose", "enabled",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
        validators = []  # unique_together enforced below for a clearer message

    def validate(self, attrs):
        platform = attrs.get("platform", getattr(self.instance, "platform", None))
        channel_id = attrs.get("channel_id", getattr(self.instance, "channel_id", None))
        qs = ChatOpsChannel.objects.filter(platform=platform, channel_id=channel_id)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"channel_id": "This channel is already configured for this platform."})
        return attrs


class ChatOpsIdentitySerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True, default=None)
    role = serializers.CharField(source="user.role", read_only=True, default=None)

    class Meta:
        model = ChatOpsIdentity
        fields = (
            "id", "platform", "platform_user_id", "platform_user_name",
            "user", "username", "role", "created_at", "updated_at",
        )
        read_only_fields = ("id", "username", "role", "created_at", "updated_at")

    def validate(self, attrs):
        platform = attrs.get("platform", getattr(self.instance, "platform", None))
        uid = attrs.get("platform_user_id", getattr(self.instance, "platform_user_id", None))
        qs = ChatOpsIdentity.objects.filter(platform=platform, platform_user_id=uid)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"platform_user_id": "This chat user is already linked for this platform."})
        return attrs


class ChatOpsIdentityLinkSerializer(serializers.Serializer):
    """Self-service claim: an authenticated spane user links a chat identity."""
    platform = serializers.ChoiceField(choices=ChatOpsPlatform.Platform.choices)
    platform_user_id = serializers.CharField(max_length=128)
    platform_user_name = serializers.CharField(max_length=128, required=False, allow_blank=True)


class ChatOpsConfigSerializer(serializers.ModelSerializer):
    # Write-only API key for the ``api`` NLP backend; stored in OpenBao at
    # spane/chatops/nlp, never returned. ``nlp_api_key_set`` reports presence only.
    nlp_api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    nlp_api_key_set = serializers.SerializerMethodField()

    class Meta:
        model = ChatOpsConfig
        fields = ("allow_unmapped_read", "require_approved_channel",
                  "nlp_provider", "nlp_endpoint", "nlp_model",
                  "nlp_api_key", "nlp_api_key_set",
                  "created_at", "updated_at")
        read_only_fields = ("created_at", "updated_at")

    def get_nlp_api_key_set(self, obj) -> bool:
        from .models import get_chatops_secret
        try:
            return bool(get_chatops_secret("nlp", "api_key"))
        except Exception:  # noqa: BLE001
            return False

    def update(self, instance, validated_data):
        api_key = validated_data.pop("nlp_api_key", None)
        for field_name, value in validated_data.items():
            setattr(instance, field_name, value)
        instance.save()
        # Only touch OpenBao when a non-blank key is supplied (so saving other
        # settings never wipes the stored key).
        if api_key:
            from .models import write_chatops_secrets
            write_chatops_secrets("nlp", {"api_key": api_key})
        return instance
