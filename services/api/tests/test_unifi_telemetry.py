"""Tests for UniFi AP telemetry collection (mapping, collection, endpoints)."""
import pytest

from apps.devices.models import Device
from apps.integrations import unifi_telemetry
from apps.integrations.models import UnifiApStatus, UnifiController

pytestmark = pytest.mark.django_db


RAW_AP = {
    "type": "uap", "mac": "aa:bb:cc:dd:ee:ff", "name": "AP-Lobby",
    "ip": "10.0.0.50", "model": "U6-Pro", "version": "7.0.35",
    "uptime": 86400, "cpu": 12, "mem": 45, "temperature": 52,
    "satisfaction": 95, "state": 1,
    "radio_table_stats": [
        {"name": "ng", "channel": 6, "channel_width": "HT20", "tx_power": 23,
         "noise": -95, "satisfaction": 97, "num_sta": 8, "cu_total": 15,
         "tx_bytes": 12345678, "rx_bytes": 87654321,
         "tx_packets": 10000, "tx_retries": 210},
        {"name": "na", "channel": 36, "channel_width": "VHT80", "tx_power": 26,
         "noise": -97, "satisfaction": 99, "num_sta": 14, "cu_total": 8,
         "tx_bytes": 22222222, "rx_bytes": 33333333,
         "tx_packets": 20000, "tx_retries": 160},
    ],
    "uplink": {"speed": 1000, "type": "wire"},
    "stat": {"tx_bytes": 123456789, "rx_bytes": 864197532},
}


RAW_GW = {
    "type": "udm", "mac": "0c:ea:14:c7:be:8d", "name": "IEAG-Brownfield",
    "ip": "10.7.40.43", "model": "UDMPROMAX", "version": "5.1.110",
    "uptime": 86400, "cpu": 8, "mem": 45, "temperature": 52, "satisfaction": 98,
    "state": 1, "num_adopted": 45, "num_disconnected": 2, "num_pending": 0,
    "sys_stats": {"loadavg_1": 0.5, "loadavg_5": 0.4, "loadavg_15": 0.3,
                  "mem_total": 2048000, "mem_used": 921600},
    "wan1": {"name": "WAN", "ip": "216.14.43.226", "up": True, "speed": 1000,
             "rx_bytes-r": 12345, "tx_bytes-r": 6789, "latency": 12, "uptime": 86000},
    "wan2": {"name": "WAN2", "ip": "10.9.9.9", "up": True, "speed": 500,
             "rx_bytes-r": 100, "tx_bytes-r": 50, "latency": 8, "uptime": 80000},
}


class FakeApClient:
    """Stand-in UnifiClient context manager returning canned AP + gateway stats."""
    def __init__(self, aps, gateway=None, health=None):
        self._aps = aps
        self._gateway = gateway
        self._health = health or []
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get_ap_stats(self):
        return self._aps
    def get_gateway_stats(self):
        return self._gateway
    def get_system_health(self):
        return self._health


def _controller(**kw):
    defaults = dict(name="HQ", host="10.0.0.1", port=8443, username="admin",
                    unifi_site_id="default")
    defaults.update(kw)
    return UnifiController.objects.create(**defaults)


class TestMapUnifiAp:
    def test_maps_health_fields(self):
        m = unifi_telemetry.map_unifi_ap(RAW_AP)
        assert m["name"] == "AP-Lobby" and m["ip"] == "10.0.0.50"
        assert m["cpu_pct"] == 12 and m["memory_pct"] == 45
        assert m["temperature_c"] == 52 and m["satisfaction"] == 95
        assert m["uptime_seconds"] == 86400 and m["is_reachable"] is True
        assert m["uplink_speed_mbps"] == 1000 and m["uplink_type"] == "wire"

    def test_client_count_sums_radios(self):
        m = unifi_telemetry.map_unifi_ap(RAW_AP)
        assert m["client_count"] == 22  # 8 + 14

    def test_radio_bands_and_retries(self):
        m = unifi_telemetry.map_unifi_ap(RAW_AP)
        bands = {r["band"]: r for r in m["radios"]}
        assert set(bands) == {"2.4GHz", "5GHz"}
        assert bands["2.4GHz"]["channel"] == 6 and bands["2.4GHz"]["clients"] == 8
        assert bands["2.4GHz"]["tx_retries_pct"] == 2.1   # 210/10000
        assert bands["5GHz"]["channel_utilization_pct"] == 8

    def test_missing_optional_fields_degrade_to_none(self):
        m = unifi_telemetry.map_unifi_ap({"type": "uap", "mac": "x", "ip": "1.2.3.4",
                                          "state": 0})
        assert m["temperature_c"] is None and m["cpu_pct"] is None
        assert m["client_count"] == 0 and m["is_reachable"] is False
        assert m["radios"] == []


class TestCollect:
    def test_collect_updates_device_and_status(self, monkeypatch):
        c = _controller()
        dev = Device.objects.create(hostname="AP-Lobby", ip_address="10.0.0.50",
                                    management_ip="10.0.0.50", platform="unifi_ap")
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeApClient([RAW_AP]))
        monkeypatch.setattr("apps.integrations.unifi_sync.get_controller_credentials",
                            lambda c, profile=None: ("admin", "pw"))
        writes = []
        monkeypatch.setattr(unifi_telemetry, "_write_influx", lambda pts: writes.extend(pts))

        res = unifi_telemetry.collect_controller_ap_telemetry(c)
        assert res == {"aps": 1, "matched": 1, "skipped": 0, "console": 0}

        st = UnifiApStatus.objects.get(device=dev)
        assert st.client_count == 22 and st.satisfaction == 95
        assert st.cpu_pct == 12 and st.uptime_seconds == 86400
        assert len(st.radios) == 2 and st.controller_id == c.id
        dev.refresh_from_db()
        assert dev.is_reachable is True and dev.last_seen is not None
        # 1 health point + 2 radio points written
        assert len(writes) == 3

    def test_collect_creates_missing_device(self, monkeypatch):
        c = _controller()
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeApClient([RAW_AP]))
        monkeypatch.setattr("apps.integrations.unifi_sync.get_controller_credentials",
                            lambda c, profile=None: ("admin", "pw"))
        monkeypatch.setattr(unifi_telemetry, "_write_influx", lambda pts: None)

        res = unifi_telemetry.collect_controller_ap_telemetry(c)
        assert res["matched"] == 1
        assert Device.objects.filter(ip_address="10.0.0.50", platform="unifi_ap").exists()

    def test_collect_all_best_effort(self, monkeypatch):
        _controller(name="A", host="10.0.0.1")
        _controller(name="B", host="10.0.0.2", enabled=False)  # skipped
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeApClient([RAW_AP]))
        monkeypatch.setattr("apps.integrations.unifi_sync.get_controller_credentials",
                            lambda c, profile=None: ("admin", "pw"))
        monkeypatch.setattr(unifi_telemetry, "_write_influx", lambda pts: None)

        totals = unifi_telemetry.collect_all_ap_telemetry()
        assert totals["controllers"] == 1 and totals["aps"] == 1


class TestConsole:
    def test_map_gateway(self):
        m = unifi_telemetry.map_unifi_gateway(RAW_GW, [])
        assert m["model"] == "UDMPROMAX" and m["cpu_pct"] == 8 and m["memory_pct"] == 45
        assert m["satisfaction"] == 98 and m["num_adopted"] == 45 and m["num_disconnected"] == 2
        assert m["loadavg_1"] == 0.5 and round(m["mem_total_mb"]) == 2000
        wans = {w["key"]: w for w in m["wans"]}
        assert set(wans) == {"wan1", "wan2"}
        assert wans["wan1"]["ip"] == "216.14.43.226" and wans["wan1"]["up"] is True
        assert wans["wan1"]["latency_ms"] == 12 and wans["wan1"]["rx_bps"] == 12345

    def test_collect_console_persists_and_writes(self, monkeypatch):
        from apps.integrations.models import UnifiConsoleStatus
        c = _controller()
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeApClient([], gateway=RAW_GW))
        monkeypatch.setattr("apps.integrations.unifi_sync.get_controller_credentials",
                            lambda c, profile=None: ("admin", "pw"))
        writes = []
        monkeypatch.setattr(unifi_telemetry, "_write_influx", lambda pts: writes.extend(pts))

        res = unifi_telemetry.collect_controller_ap_telemetry(c)
        assert res["console"] == 1
        dev = Device.objects.get(ip_address="10.7.40.43")
        assert dev.platform == "unifi_udm"
        st = UnifiConsoleStatus.objects.get(device=dev)
        assert st.cpu_pct == 8 and st.num_adopted == 45 and len(st.wans) == 2
        assert st.controller_id == c.id
        # 1 health point + 2 WAN points
        assert len(writes) == 3

    def test_device_unifi_console_endpoint(self, auth_client, monkeypatch):
        from apps.integrations.models import UnifiConsoleStatus
        dev = Device.objects.create(hostname="udm-1", ip_address="10.7.40.43", platform="unifi_udm")
        UnifiConsoleStatus.objects.create(device=dev, state=1, cpu_pct=8, num_adopted=45,
                                          wans=[{"key": "wan1", "ip": "1.2.3.4", "up": True}])
        monkeypatch.setattr("apps.integrations.unifi_telemetry.query_console_timeseries",
                            lambda did, period: {"device_id": did, "period": period, "health": {}, "wan": {}})
        resp = auth_client.get(f"/api/devices/{dev.id}/unifi-console/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"]["cpu_pct"] == 8 and body["status"]["num_adopted"] == 45
        assert body["status"]["wans"][0]["ip"] == "1.2.3.4"


class TestEndpoints:
    def test_wireless_summary_endpoint(self, auth_client):
        c = _controller()
        dev = Device.objects.create(hostname="AP-1", ip_address="10.0.0.50",
                                    platform="unifi_ap")
        UnifiApStatus.objects.create(
            device=dev, controller=c, state=1, satisfaction=90, client_count=12,
            radios=[{"band": "5GHz", "channel": 36, "channel_utilization_pct": 8}],
        )
        resp = auth_client.get("/api/wireless/summary/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_aps"] == 1 and body["online"] == 1 and body["offline"] == 0
        assert body["total_clients"] == 12 and body["avg_satisfaction"] == 90
        assert body["aps"][0]["hostname"] == "AP-1"
        assert body["channel_utilization"]["5GHz"]["36"] == {"utilization_pct": 8, "ap_count": 1}

    def test_wireless_aps_endpoint(self, auth_client):
        dev = Device.objects.create(hostname="AP-1", ip_address="10.0.0.50",
                                    platform="unifi_ap")
        UnifiApStatus.objects.create(device=dev, state=1, client_count=3, radios=[])
        resp = auth_client.get("/api/wireless/aps/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1 and data[0]["client_count"] == 3
        assert data[0]["source"] == "unifi" and data[0]["vendor"] == "UniFi"

    def test_wireless_includes_mist_aps_from_inventory(self, auth_client):
        # UniFi AP has a telemetry snapshot; the Mist AP is inventory-only (no
        # snapshot) but must still appear, built from the Device record.
        u = Device.objects.create(hostname="uap-1", ip_address="10.0.0.50", platform="unifi_ap")
        UnifiApStatus.objects.create(device=u, state=1, client_count=3, radios=[])
        Device.objects.create(hostname="mist-ap-1", ip_address="10.0.0.60",
                              platform="mist_ap", is_reachable=True)
        resp = auth_client.get("/api/wireless/aps/")
        assert resp.status_code == 200
        by_host = {a["hostname"]: a for a in resp.json()}
        assert set(by_host) == {"uap-1", "mist-ap-1"}
        mist = by_host["mist-ap-1"]
        assert mist["source"] == "mist" and mist["vendor"] == "Mist"
        assert mist["state"] == 1 and mist["radios"] == [] and mist["satisfaction"] is None

    def test_wireless_offline_mist_ap_reflects_reachability(self, auth_client):
        Device.objects.create(hostname="mist-down", ip_address="10.0.0.61",
                              platform="mist_ap", is_reachable=False)
        body = auth_client.get("/api/wireless/summary/").json()
        assert body["total_aps"] == 1 and body["online"] == 0 and body["offline"] == 1
        assert body["aps"][0]["state"] == 0

    def test_wireless_excludes_switches_and_gateways(self, auth_client):
        # Switches/gateways/consoles belong on Network Devices, not Wireless.
        for i, (host, plat) in enumerate([("mist-sw", "mist_sw"), ("mist-gw", "mist_gw"),
                                          ("unifi-sw", "unifi_sw"), ("unifi-udm", "unifi_udm")]):
            Device.objects.create(hostname=host, ip_address=f"10.0.1.{i + 1}", platform=plat)
        Device.objects.create(hostname="mist-ap", ip_address="10.0.1.99", platform="mist_ap")
        hosts = {a["hostname"] for a in auth_client.get("/api/wireless/aps/").json()}
        assert hosts == {"mist-ap"}  # only the AP

    def test_device_unifi_ap_endpoint(self, auth_client, monkeypatch):
        dev = Device.objects.create(hostname="AP-2", ip_address="10.0.0.51",
                                    platform="unifi_ap")
        UnifiApStatus.objects.create(device=dev, state=1, client_count=5,
                                     radios=[{"band": "2.4GHz", "channel": 6}])
        monkeypatch.setattr("apps.integrations.unifi_telemetry.query_ap_timeseries",
                            lambda did, period: {"device_id": did, "period": period,
                                                 "radios": {}, "clients_total": []})
        resp = auth_client.get(f"/api/devices/{dev.id}/unifi-ap/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"]["client_count"] == 5
        assert body["timeseries"]["period"] == "1h"

    def test_device_unifi_ap_endpoint_no_status(self, auth_client, monkeypatch):
        dev = Device.objects.create(hostname="AP-3", ip_address="10.0.0.52",
                                    platform="unifi_ap")
        monkeypatch.setattr("apps.integrations.unifi_telemetry.query_ap_timeseries",
                            lambda did, period: {"radios": {}, "clients_total": []})
        resp = auth_client.get(f"/api/devices/{dev.id}/unifi-ap/")
        assert resp.status_code == 200
        assert resp.json()["status"] is None
