import ipaddress

from rest_framework import serializers

from .models import WanCircuit


def _validate_cidr(value: str) -> str:
    if not value:
        return value
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        raise serializers.ValidationError(f"'{value}' is not a valid CIDR block.")
    return value


class WanCircuitSerializer(serializers.ModelSerializer):
    device_hostname = serializers.CharField(source="device.hostname", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    circuit_type_display = serializers.CharField(source="get_circuit_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    bandwidth_mbps = serializers.IntegerField(read_only=True)
    upload_mbps = serializers.IntegerField(read_only=True)

    class Meta:
        model = WanCircuit
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def validate_isp_ipv4_block(self, value):
        return _validate_cidr(value)

    def validate_isp_ipv6_block(self, value):
        return _validate_cidr(value)
