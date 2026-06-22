from __future__ import annotations

from rest_framework import serializers

from .models import ConfigPushTemplate
from .render import detect_variables, is_sensitive


class ConfigPushTemplateSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    detected_variables = serializers.SerializerMethodField()

    class Meta:
        model = ConfigPushTemplate
        fields = [
            "id", "name", "description", "category", "platform",
            "template_content", "variables", "detected_variables",
            "enabled", "builtin", "created_by_username", "created_at", "updated_at",
        ]
        read_only_fields = ["builtin", "created_by_username", "created_at", "updated_at"]

    def get_detected_variables(self, obj) -> list[dict]:
        """Variables referenced by the template, flagged sensitive for the UI."""
        return [
            {"name": name, "sensitive": is_sensitive(name)}
            for name in detect_variables(obj.template_content)
        ]

    def to_representation(self, instance):
        """Never echo sensitive default values back to the client."""
        data = super().to_representation(instance)
        variables = data.get("variables") or {}
        data["variables"] = {
            key: ("" if is_sensitive(key) else value) for key, value in variables.items()
        }
        return data

    def _persist_variables(self, instance, raw_variables):
        """Split incoming variables into DB (non-sensitive) + OpenBao (sensitive)."""
        instance.store_variables(raw_variables or {})
        instance.save(update_fields=["variables", "updated_at"])

    def create(self, validated_data):
        raw_variables = validated_data.pop("variables", {})
        instance = super().create({**validated_data, "variables": {}})
        self._persist_variables(instance, raw_variables)
        return instance

    def update(self, instance, validated_data):
        raw_variables = validated_data.pop("variables", None)
        instance = super().update(instance, validated_data)
        if raw_variables is not None:
            self._persist_variables(instance, raw_variables)
        return instance
