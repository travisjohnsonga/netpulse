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


class TestReachabilityEndpoint:
    def test_requires_auth(self, api_client, device):
        assert api_client.get(f"/api/devices/{device.id}/reachability/").status_code == 401

    def test_returns_query_result(self, auth_client, device, monkeypatch):
        from apps.devices import metrics_influx
        sample = {"device_id": str(device.id), "period": "24h", "current": True,
                  "rtt_ms": 2.3, "uptime_pct_24h": 99.8, "avg_rtt_ms": 2.1,
                  "max_rtt_ms": 45.2, "data": [{"time": "t", "rtt_ms": 2.1, "reachable": True}]}
        captured = {}
        monkeypatch.setattr(metrics_influx, "query_reachability",
                            lambda d, p: captured.update(d=d, p=p) or sample)
        resp = auth_client.get(f"/api/devices/{device.id}/reachability/?period=24h")
        assert resp.status_code == 200
        body = resp.json()
        assert body["uptime_pct_24h"] == 99.8 and body["rtt_ms"] == 2.3
        assert captured == {"d": str(device.id), "p": "24h"}

    def test_invalid_period_degrades(self, monkeypatch):
        from apps.devices import metrics_influx
        monkeypatch.setattr(metrics_influx, "_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        out = metrics_influx.query_reachability("3", "999d")
        assert out["period"] == "1h"          # bad period normalised
        assert out["rtt_ms"] is None and out["data"] == []  # degrades, no raise


class TestReachabilitySummary:
    def test_requires_auth(self, api_client):
        assert api_client.get("/api/devices/reachability-summary/").status_code == 401

    def test_returns_total_and_data(self, auth_client, device, monkeypatch):
        from apps.devices import metrics_influx
        sample = {"period": "6h", "total_devices": 3,
                  "data": [{"time": "t", "active": 2, "unreachable": 1}]}
        captured = {}
        monkeypatch.setattr(metrics_influx, "query_reachability_summary",
                            lambda p: captured.update(p=p) or sample)
        body = auth_client.get("/api/devices/reachability-summary/?period=6h").json()
        assert body["total_devices"] == 3 and body["data"][0]["active"] == 2
        assert captured["p"] == "6h"

    def test_summary_counts_active_unreachable_devices_and_degrades(self, monkeypatch):
        # InfluxDB down → total from the DB (active+unreachable), data empty.
        from apps.devices import metrics_influx
        from apps.devices.models import Device
        Device.objects.create(hostname="a1", ip_address="10.0.0.1", status="active")
        Device.objects.create(hostname="u1", ip_address="10.0.0.2", status="unreachable")
        Device.objects.create(hostname="i1", ip_address="10.0.0.3", status="inactive")  # excluded
        monkeypatch.setattr(metrics_influx, "_client", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        out = metrics_influx.query_reachability_summary("999d")
        assert out["period"] == "1h"           # normalised
        assert out["total_devices"] == 2       # active + unreachable only
        assert out["data"] == []


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

    def test_field_map_fortinet(self):
        from apps.devices.metrics_influx import FIELD_MAP
        assert FIELD_MAP["1_3_6_1_4_1_12356_101_4_1_3_0"] == "cpu_pct"
        assert FIELD_MAP["1_3_6_1_4_1_12356_101_4_1_4_0"] == "memory_used_pct"
        assert FIELD_MAP["1_3_6_1_4_1_12356_101_4_1_5_0"] == "memory_total_kb"

    def test_field_map_sonicwall_aruba(self):
        from apps.devices.metrics_influx import FIELD_MAP
        assert FIELD_MAP["1_3_6_1_4_1_8741_1_3_1_3_0"] == "cpu_pct"          # SonicWall CPU %
        assert FIELD_MAP["1_3_6_1_4_1_8741_1_3_1_4_0"] == "memory_used_pct"  # SonicWall RAM %
        assert FIELD_MAP["1_3_6_1_4_1_8741_1_3_1_2_0"] == "connections"      # SonicWall conn count
        assert FIELD_MAP["1_3_6_1_4_1_14823_2_2_1_1_1_11_0"] == "cpu_pct"    # wlsxSysXCpuUtilization
        assert FIELD_MAP["1_3_6_1_4_1_14823_2_2_1_1_1_10_0"] == "memory_used_pct"

    def test_fortinet_cpu_mem_surfaced(self, monkeypatch):
        # FortiGate reports CPU% and memory% directly (not bytes) — both surface.
        from apps.devices import metrics_influx as mi

        class _C:
            def query_api(self): return None
            def close(self): pass
        monkeypatch.setattr(mi, "_client", lambda: _C())
        monkeypatch.setattr(mi, "_latest_snapshot", lambda *a: {
            "cpu_pct": 12.0, "memory_used_pct": 47.0, "memory_total_kb": 2048000,
            "uptime_seconds": 100.0})
        monkeypatch.setattr(mi, "_timeseries", lambda *a: {"uptime": [], "memory_used_pct": [], "cpu_pct": []})
        monkeypatch.setattr(mi, "_interface_stats", lambda *a: [])
        monkeypatch.setattr(mi, "_reachability", lambda *a: mi._empty_reachability())
        out = mi.query_device_metrics("3", "all", "1h")
        assert out["metrics"]["cpu_pct"] == 12.0
        assert out["metrics"]["memory_used_pct"] == 47.0
        assert out["metrics"]["memory_total_bytes"] == 2048000 * 1024

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

    def test_environment_prefers_explicit_scalars(self):
        # AOS-CX: stream-processor derives temp_max_c/fan_count/psu_count — these
        # win over token-scanning (which would mis-count the *_count fields).
        from apps.devices.metrics_influx import _environment
        snap = {"temp_max_c": 28.88, "fan_count": 4, "psu_count": 1,
                "cpu_pct": 22.5, "GigabitEthernet1/in_octets": 99}
        env = _environment(snap)
        assert env == {"temperature_c": 28.9, "fan_count": 4, "psu_count": 1}


class _FakeRec:
    def __init__(self, values):
        self.values = values


class _FakeTable:
    def __init__(self, records):
        self.records = records


class _FakeQueryApi:
    """Minimal influxdb_client query_api stand-in: one table of pivoted rows."""
    def __init__(self, rows):
        self._rows = rows

    def query(self, flux):
        return [_FakeTable([_FakeRec(r) for r in self._rows])]


class TestStatusOkFromText:
    def test_truthy_states(self):
        from apps.devices.metrics_influx import _status_ok_from_text
        assert _status_ok_from_text("ok") is True
        assert _status_ok_from_text("online") is True

    def test_falsy_states(self):
        from apps.devices.metrics_influx import _status_ok_from_text
        assert _status_ok_from_text("fault") is False
        assert _status_ok_from_text("offline") is False

    def test_unknown_is_none(self):
        from apps.devices.metrics_influx import _status_ok_from_text
        assert _status_ok_from_text("unknown") is None
        assert _status_ok_from_text(None) is None


class TestEnvironmentDetail:
    def test_buckets_temperature_fans_psus_and_poe(self):
        from apps.devices.metrics_influx import _environment_detail
        rows = [
            {"sensor_name": "Temp Sensor", "sensor_type": "temperature",
             "temperature_c": 26.8, "status_ok": 1},
            {"sensor_name": "System-1/1/1", "sensor_type": "fan",
             "fan_rpm": -1.0, "status": "ok"},
            {"sensor_name": "System-1/1/2", "sensor_type": "fan",
             "fan_rpm": 4500.0, "status": "ok"},
            {"sensor_name": "1/1", "sensor_type": "psu",
             "watts": 0.0, "status": "online"},
            {"sensor_name": "poe", "sensor_type": "poe",
             "poe_budget_watts": 740.0, "poe_used_watts": 56.0, "poe_status": "on"},
        ]
        out = _environment_detail(_FakeQueryApi(rows), "metrics", "1", "1h")
        assert out["sensors"][0]["temperature_c"] == 26.8
        fans = {f["name"]: f for f in out["fans"]}
        assert fans["System-1/1/1"]["rpm"] is None        # -1 → unavailable
        assert fans["System-1/1/2"]["rpm"] == 4500
        assert all(f["status_ok"] is True for f in out["fans"])
        assert out["psus"][0] == {"name": "1/1", "watts": 0.0, "status_ok": True}
        assert out["poe"]["budget_watts"] == 740.0
        assert out["poe"]["used_pct"] == 7.6              # derived 56/740
        assert out["poe"]["status"] == "on"

    def test_no_environment_rows(self):
        from apps.devices.metrics_influx import _environment_detail
        out = _environment_detail(_FakeQueryApi([]), "metrics", "1", "1h")
        assert out == {"sensors": [], "fans": [], "psus": [], "poe": None}


class TestPingSummary:
    def test_requires_auth(self, api_client):
        assert api_client.get("/api/devices/ping-summary/").status_code == 401

    def test_returns_and_caches(self, auth_client, monkeypatch):
        from django.core.cache import cache
        from apps.devices import metrics_influx
        cache.delete("ping_summary")
        calls = {"n": 0}

        def fake():
            calls["n"] += 1
            return [{"device_id": 1, "current_ms": 0.9, "avg_ms": 1.1, "max_ms": 2.5,
                     "uptime_pct": 100.0, "sparkline": [0.9, 1.1, None, 0.8]}]
        monkeypatch.setattr(metrics_influx, "query_ping_summary", fake)

        r1 = auth_client.get("/api/devices/ping-summary/")
        assert r1.status_code == 200 and r1.data[0]["current_ms"] == 0.9
        assert r1.data[0]["sparkline"] == [0.9, 1.1, None, 0.8]
        auth_client.get("/api/devices/ping-summary/")        # second hit
        assert calls["n"] == 1                                # served from 60s cache
        cache.delete("ping_summary")
