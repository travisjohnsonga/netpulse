from rest_framework import serializers

from .models import CVE, DeviceCVE


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
