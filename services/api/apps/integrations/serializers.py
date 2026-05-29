from rest_framework import serializers

from .models import NetBoxImport


class NetBoxImportSerializer(serializers.ModelSerializer):
    class Meta:
        model = NetBoxImport
        fields = (
            "id", "netbox_url", "netbox_version", "status", "options",
            "sites_imported", "devices_imported", "skipped", "errors",
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
