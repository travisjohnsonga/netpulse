from rest_framework import serializers

from .models import LogFilter


class LogFilterSerializer(serializers.ModelSerializer):
    class Meta:
        model = LogFilter
        fields = (
            "id", "name", "pattern", "action", "color", "tag",
            "platforms", "enabled", "created_at",
        )
        read_only_fields = ("created_at",)

    def validate_pattern(self, value):
        import re
        try:
            re.compile(value)
        except re.error:
            raise serializers.ValidationError("Invalid regular expression.")
        return value


class LogFilterTestSerializer(serializers.Serializer):
    pattern = serializers.CharField()
    message = serializers.CharField(allow_blank=True)
