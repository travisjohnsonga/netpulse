import pytest

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    from apps.devices.models import Device
    return Device.objects.create(hostname="router1", ip_address="192.168.98.100", status="active")


class TestMetricsEndpoint:
    def test_requires_auth(self, api_client, device):
        assert api_client.get(f"/api/devices/{device.id}/metrics/").status_code == 401

    def test_returns_query_result(self, auth_client, device, monkeypatch):
        from apps.devices import metrics_influx
        sample = {"device_id": str(device.id), "period": "6h",
                  "metrics": {"uptime_seconds": 194185.8, "memory_used_pct": 9.6, "cpu_pct": None},
                  "timeseries": {"uptime": [], "memory_used_pct": [], "cpu_pct": []}, "interfaces": {}}
        called = {}
        def fake(dev_id, metric, period):
            called.update(dev_id=dev_id, metric=metric, period=period)
            return sample
        monkeypatch.setattr(metrics_influx, "query_device_metrics", fake)
        resp = auth_client.get(f"/api/devices/{device.id}/metrics/?metric=memory&period=6h")
        assert resp.status_code == 200
        assert resp.json()["metrics"]["memory_used_pct"] == 9.6
        assert called == {"dev_id": str(device.id), "metric": "memory", "period": "6h"}

    def test_default_period_is_1h(self, auth_client, device, monkeypatch):
        from apps.devices import metrics_influx
        captured = {}
        monkeypatch.setattr(metrics_influx, "query_device_metrics",
                            lambda d, m, p: captured.update(p=p) or metrics_influx._empty(d, p))
        auth_client.get(f"/api/devices/{device.id}/metrics/")
        assert captured["p"] == "1h"


class TestMetricsModule:
    def test_pct_used(self):
        from apps.devices.metrics_influx import _pct_used
        assert _pct_used(200121296, 1878994416) == 9.6
        assert _pct_used(None, 5) is None
        assert _pct_used(0, 0) is None

    def test_invalid_period_normalised_and_degrades(self, monkeypatch):
        # Bad period → 1h; InfluxDB client error → empty structure (no raise).
        from apps.devices import metrics_influx
        monkeypatch.setattr(metrics_influx, "_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        out = metrics_influx.query_device_metrics("3", "all", "999d")
        assert out["period"] == "1h"
        assert out["metrics"]["uptime_seconds"] is None
        assert out["timeseries"] == {"uptime": [], "memory_used_pct": [], "cpu_pct": []}

    def test_field_map_covers_spec(self):
        from apps.devices.metrics_influx import FIELD_MAP
        assert FIELD_MAP["sysUpTime_0"] == "uptime_seconds"
        assert FIELD_MAP["1_3_6_1_4_1_9_9_48_1_1_1_5_1"] == "memory_used_bytes"
        assert FIELD_MAP["1_3_6_1_4_1_9_9_109_1_1_1_1_8_1"] == "cpu_5min_pct"

    def test_field_map_gnmi_memory_and_cpu(self):
        # Cisco IOS-XE gNMI memory-statistics + cpu-utilization field names.
        from apps.devices.metrics_influx import FIELD_MAP
        assert FIELD_MAP["Processor/used_memory"] == "memory_used_bytes"
        assert FIELD_MAP["Processor/free_memory"] == "memory_free_bytes"
        assert FIELD_MAP["Processor/total_memory"] == "memory_total_bytes"
        assert FIELD_MAP["five_seconds"] == "cpu_5sec_pct"
        assert FIELD_MAP["one_minute"] == "cpu_1min_pct"
        assert FIELD_MAP["five_minutes"] == "cpu_5min_pct"

    def test_mem_used_pct_prefers_total(self):
        from apps.devices.metrics_influx import _mem_used_pct
        # gNMI Processor pool: used/total (router2 values).
        assert _mem_used_pct(199360764, 1879754948, 2079115712) == 9.6
        # No total → fall back to used/(used+free) (SNMP pool).
        assert _mem_used_pct(200121296, 1878994416, None) == 9.6
        # Nothing usable → None.
        assert _mem_used_pct(None, None, None) is None

    def test_environment_empty_for_virtual_device(self):
        # A device that reports only interface counters (e.g. virtual C8000V)
        # has no fan/power/temperature sensors → empty environment.
        from apps.devices.metrics_influx import _environment
        snap = {"GigabitEthernet1/in_octets": 1000, "sysUpTime_0": 500.0,
                "ciscoMemoryPoolUsed_1": 2000}
        assert _environment(snap) == {}

    def test_environment_extracts_sensors_when_present(self):
        from apps.devices.metrics_influx import _environment
        snap = {
            "Sensor1/temperature": 38.0, "Sensor2/temperature": 41.5,
            "PSU1/fan_speed": 4200, "PowerSupply1/power_in": 120,
            "GigabitEthernet1/in_octets": 99,
        }
        env = _environment(snap)
        assert env["temperature_c"] == 41.5 and env["temperature_sensors"] == 2
        assert env["fan_sensors"] == 1
        assert env["power_sensors"] == 1
