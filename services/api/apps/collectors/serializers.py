from rest_framework import serializers

from .models import Collector


class CollectorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Collector
        fields = "__all__"
        read_only_fields = ("api_key_hash", "cert_serial", "cert_expires_at", "last_seen_at", "created_at", "updated_at")
