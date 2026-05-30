from rest_framework import serializers

from .models import Collector


class CollectorSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    device_count = serializers.IntegerField(source="devices.count", read_only=True)

    class Meta:
        model = Collector
        fields = "__all__"
        read_only_fields = ("api_key_hash", "cert_serial", "cert_expires_at", "last_seen_at", "created_at", "updated_at")
