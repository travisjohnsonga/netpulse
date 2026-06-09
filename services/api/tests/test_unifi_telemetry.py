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


class FakeApClient:
    """Stand-in UnifiClient context manager returning canned AP stats."""
    def __init__(self, aps):
        self._aps = aps
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def get_ap_stats(self):
        return self._aps


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
        monkeypatch.setattr("apps.integrations.unifi_sync._credentials",
                            lambda c, p="": ("admin", "pw"))
        writes = []
        monkeypatch.setattr(unifi_telemetry, "_write_influx", lambda pts: writes.extend(pts))

        res = unifi_telemetry.collect_controller_ap_telemetry(c)
        assert res == {"aps": 1, "matched": 1, "skipped": 0}

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
        monkeypatch.setattr("apps.integrations.unifi_sync._credentials",
                            lambda c, p="": ("admin", "pw"))
        monkeypatch.setattr(unifi_telemetry, "_write_influx", lambda pts: None)

        res = unifi_telemetry.collect_controller_ap_telemetry(c)
        assert res["matched"] == 1
        assert Device.objects.filter(ip_address="10.0.0.50", platform="unifi_ap").exists()

    def test_collect_all_best_effort(self, monkeypatch):
        _controller(name="A", host="10.0.0.1")
        _controller(name="B", host="10.0.0.2", enabled=False)  # skipped
        monkeypatch.setattr("apps.integrations.unifi_client.UnifiClient",
                            lambda *a, **k: FakeApClient([RAW_AP]))
        monkeypatch.setattr("apps.integrations.unifi_sync._credentials",
                            lambda c, p="": ("admin", "pw"))
        monkeypatch.setattr(unifi_telemetry, "_write_influx", lambda pts: None)

        totals = unifi_telemetry.collect_all_ap_telemetry()
        assert totals["controllers"] == 1 and totals["aps"] == 1


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
