"""Integration: telemetry — metrics, interfaces, reachability, config generate."""
import pytest

from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    return Device.objects.create(
        hostname="tlm-rtr", ip_address="10.7.0.1",
        vendor="Cisco", platform=Device.Platform.IOS_XE, status="active",
    )


class TestMetricsEndpoint:
    def test_metrics_returns_documented_subkeys(self, auth_client, device):
        # InfluxDB is unavailable in tests → endpoint returns the empty shape.
        resp = auth_client.get(f"/api/devices/{device.id}/metrics/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_id"] == str(device.id)
        # Metrics live under a "metrics" sub-dict, not at the top level.
        metrics = body["metrics"]
        for key in ("uptime_seconds", "memory_used_pct", "cpu_pct",
                    "memory_total_bytes", "poll_duration_ms"):
            assert key in metrics
        assert "timeseries" in body
        assert "interfaces" in body
        assert "reachability" in body

    def test_requires_auth(self, api_client, device):
        assert api_client.get(f"/api/devices/{device.id}/metrics/").status_code == 401


class TestInterfacesEndpoint:
    def test_list_empty(self, auth_client, device):
        resp = auth_client.get(f"/api/devices/{device.id}/interfaces/")
        assert resp.status_code == 200
        # No monitored interfaces yet.
        data = resp.json()
        interfaces = data if isinstance(data, list) else data.get("results", data)
        assert interfaces == [] or interfaces == {} or len(interfaces) == 0


class TestReachabilityEndpoint:
    def test_shape(self, auth_client, device):
        resp = auth_client.get(f"/api/devices/{device.id}/reachability/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["device_id"] == str(device.id)


class TestTelemetryConfigGenerate:
    def test_generated_config_is_ascii(self, auth_client, device, settings):
        settings.COLLECTOR_IP = "192.168.98.134"
        resp = auth_client.get(f"/api/devices/{device.id}/telemetry-config/generate/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert "sections" in body
        assert "full_config" in body
        # sanitize_config_for_push must yield pure ASCII.
        body["full_config"].encode("ascii")  # raises if any non-ASCII slipped through
        # Collector IP referenced in the generated SNMP/syslog config.
        assert "192.168.98.134" in body["full_config"]

    def test_requires_auth(self, api_client, device):
        assert api_client.get(
            f"/api/devices/{device.id}/telemetry-config/generate/"
        ).status_code == 401
