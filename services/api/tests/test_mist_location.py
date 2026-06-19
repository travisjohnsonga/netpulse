"""Tests for the Mist warehouse-location dashboard (apps.integrations.mist_location).

Field names / shapes here mirror a real gc4-org response (maps, stats/devices,
stats/clients, sle/.../summary) verified live — see mist_location.py's module
docstring. The Mist HTTP calls are faked; nothing hits the network.
"""
import json

import pytest
from django.core.cache import cache

from apps.integrations import mist_location
from apps.integrations.models import MistSite

pytestmark = pytest.mark.django_db


# ── Canned Mist responses (real field names) ─────────────────────────────────
MAPS = [{
    "id": "map-1", "name": "Warehouse 2/3", "type": "image",
    "url": "https://api.gc4.mist.com/api/v1/forward/download?jwt=signedimg",
    "width": 2805, "height": 1566, "width_m": 326.4, "height_m": 182.2, "ppm": 8.59,
}]

# /sites/{s}/stats/devices — APs carry placement (pixel x/y), status, num_clients.
DEVICE_STATS = [
    {"type": "ap", "mac": "7cb68d30da49", "name": "wco2-wh-ap-08", "status": "connected",
     "map_id": "map-1", "x": 915.5, "y": 718.3, "num_clients": 3},
    {"type": "ap", "mac": "7cb68d30daa3", "name": "wco2-wh-ap-05", "status": "disconnected",
     "map_id": "map-1", "x": 978.9, "y": 439.0, "num_clients": 0},
    {"type": "ap", "mac": "aaaaaaaaaaaa", "name": "other-floor-ap", "status": "connected",
     "map_id": "map-OTHER", "x": 10, "y": 10, "num_clients": 1},
    # A switch shares the stats/devices feed — _ap_stats must drop non-APs.
    {"type": "switch", "mac": "cccccccccccc", "name": "a-switch", "status": "connected",
     "map_id": "map-1", "x": 5, "y": 5},
]

# An AP placed on the map but missing coordinates (rare/misconfigured): counted
# in aps_total but never drawn as a marker.
AP_NO_COORDS = {"type": "ap", "mac": "bbbbbbbbbbbb", "name": "unplaced-ap",
                "status": "connected", "map_id": "map-1", "x": None, "y": None}

# /sites/{s}/stats/clients — located WiFi clients (pixel x/y + tx_bps/rx_bps).
CLIENTS = [
    {"mac": "4c115478afc8", "hostname": "Memor-11", "map_id": "map-1", "x": 924.4, "y": 649.5,
     "band": "5", "rssi": -59, "ap_mac": "7cb68d30da49", "num_locating_aps": 1,
     "last_seen": 1781843280, "tx_bps": 2_000_000, "rx_bps": 1_000_000,
     "tx_rate": 150.0, "rx_rate": 6.0},
    {"mac": "deadbeef0001", "hostname": "TC52-02", "map_id": "map-1", "x": 100.0, "y": 200.0,
     "band": "24", "rssi": -70, "ap_mac": "7cb68d30da49", "num_locating_aps": 2,
     "last_seen": 1781843281, "tx_bps": 500_000, "rx_bps": 500_000},
    # Associated to this map but not located (no x/y) → counted in clients_total,
    # excluded from the markers list.
    {"mac": "deadbeef0002", "hostname": "TC52-03", "map_id": "map-1", "x": None, "y": None,
     "band": "5", "tx_bps": 0, "rx_bps": 0},
    # On a different map → excluded everywhere (still adds to site throughput).
    {"mac": "deadbeef0003", "hostname": "Elsewhere", "map_id": "map-OTHER", "x": 1, "y": 1,
     "band": "5", "tx_bps": 1_000_000, "rx_bps": 0},
]

SLE_OK = {"sle": {"name": "coverage", "samples": {
    "total": [100.0, 100.0, 80.0, None], "degraded": [0.0, 5.0, 0.0, None]}}}
SLE_EMPTY = {"sle": {"name": "roaming", "samples": {"total": [None, None], "degraded": [None, None]}}}


class FakeMist:
    """Stand-in MistClient: dispatches _get by path and serves get_device_stats."""

    def __init__(self, maps=MAPS, devices=DEVICE_STATS, clients=CLIENTS,
                 sle=SLE_OK, fail_sle=False):
        self._maps = maps
        self._devices = devices
        self._clients = clients
        self._sle = sle
        self._fail_sle = fail_sle

    def get_device_stats(self, site_id):
        return self._devices

    def _get(self, path, timeout=None):
        if path.endswith("/maps"):
            return self._maps
        if path.endswith("/stats/clients"):
            return self._clients
        if "/sle/" in path:
            if self._fail_sle:
                raise RuntimeError("sle boom")
            return self._sle
        raise AssertionError(f"unexpected path {path}")


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


# ── Unit: helpers ────────────────────────────────────────────────────────────
class TestHelpers:
    def test_map_meta_uses_pixel_dims(self):
        meta = mist_location._map_meta(MAPS[0])
        assert meta["id"] == "map-1" and meta["image_url"].startswith("https://")
        assert meta["width"] == 2805 and meta["height"] == 1566

    def test_pick_map_default_and_by_id(self):
        assert mist_location._pick_map(MAPS, None)["id"] == "map-1"
        assert mist_location._pick_map(MAPS, "map-1")["id"] == "map-1"
        assert mist_location._pick_map(MAPS, "nope") is None
        assert mist_location._pick_map([], None) is None

    def test_ap_stats_filters_non_aps(self):
        aps = mist_location._ap_stats(FakeMist(), "site-1")
        assert {a["mac"] for a in aps} == {"7cb68d30da49", "7cb68d30daa3", "aaaaaaaaaaaa"}
        assert all(a.get("type") == "ap" for a in aps)

    def test_ap_markers_filter_map_and_nulls(self):
        # Fed AP-only rows plus the coord-less AP; markers keep on-map, located APs.
        aps = mist_location._ap_markers(mist_location._ap_stats(FakeMist(), "s") + [AP_NO_COORDS], "map-1")
        names = {a["name"] for a in aps}
        assert names == {"wco2-wh-ap-08", "wco2-wh-ap-05"}  # other-floor + unplaced excluded
        a8 = next(a for a in aps if a["name"] == "wco2-wh-ap-08")
        assert a8["x"] == 915.5 and a8["status"] == "connected" and a8["clients"] == 3

    def test_client_markers_filter_and_throughput(self):
        markers, tput = mist_location._client_markers(CLIENTS, "map-1")
        macs = {c["mac"] for c in markers}
        assert macs == {"4c115478afc8", "deadbeef0001"}  # only located, on this map
        # Throughput is summed across ALL clients (tx_bps+rx_bps) → Mbps.
        # (2M+1M)+(0.5M+0.5M)+(0)+(1M+0) = 5.0 Mbps
        assert tput == 5.0
        first = next(c for c in markers if c["mac"] == "4c115478afc8")
        assert first["band"] == "5" and first["rssi"] == -59 and first["num_locating_aps"] == 1

    def test_sle_success_rate(self):
        # 1 - (0+5+0)/(100+100+80) = 1 - 5/280 = 0.982 → 98%
        assert mist_location._sle_summary(FakeMist(), "s", "coverage") == 98

    def test_sle_none_when_no_samples(self):
        assert mist_location._sle_summary(FakeMist(sle=SLE_EMPTY), "s", "roaming") is None

    def test_sle_none_on_error(self):
        assert mist_location._sle_summary(FakeMist(fail_sle=True), "s", "coverage") is None


# ── Unit: build_payload ──────────────────────────────────────────────────────
class TestBuildPayload:
    def test_shape_and_counts(self):
        p = mist_location.build_payload(FakeMist(), "site-1")
        assert p["map"]["id"] == "map-1"
        assert len(p["aps"]) == 2
        assert len(p["clients"]) == 2
        s = p["summary"]
        assert s["clients_online"] == 2       # located on this map
        assert s["clients_total"] == 3        # associated to this map (incl. un-located)
        assert s["aps_total"] == 2            # placed on this map
        assert s["aps_online"] == 1           # only ap-08 connected
        assert s["throughput_mbps"] == 5.0
        # SLE keys are dashes→underscores; coverage computed, others present.
        assert set(p["sle"]) == {"roaming", "coverage", "time_to_connect", "throughput"}
        assert p["sle"]["coverage"] == 98
        assert isinstance(p["generated"], int)

    def test_coordless_ap_counts_in_total_not_markers(self):
        fm = FakeMist(devices=DEVICE_STATS + [AP_NO_COORDS])
        p = mist_location.build_payload(fm, "site-1")
        assert p["summary"]["aps_total"] == 3 and len(p["aps"]) == 2
        assert p["summary"]["aps_online"] == 2  # ap-08 + unplaced both connected

    def test_no_map_raises(self):
        with pytest.raises(ValueError):
            mist_location.build_payload(FakeMist(maps=[]), "site-1")

    def test_payload_carries_no_token(self):
        p = mist_location.build_payload(FakeMist(), "site-1")
        blob = json.dumps(p)
        assert "Token " not in blob and "Authorization" not in blob


# ── Unit: cache + refresh ────────────────────────────────────────────────────
class TestRefresh:
    def test_refresh_caches_and_get_cached(self, monkeypatch):
        monkeypatch.setattr(mist_location, "_mist_client", lambda: (FakeMist(), object()))
        out = mist_location.refresh("site-1")
        assert out is not None and out["map"]["id"] == "map-1"
        cached = mist_location.get_cached("site-1", "map-1")
        assert cached is not None and cached["summary"]["aps_online"] == 1

    def test_refresh_none_when_not_configured(self, monkeypatch):
        monkeypatch.setattr(mist_location, "_mist_client", lambda: (None, None))
        assert mist_location.refresh("site-1") is None

    def test_refresh_all_iterates_sites(self, monkeypatch):
        MistSite.objects.create(mist_id="site-1", name="WH1")
        MistSite.objects.create(mist_id="site-2", name="WH2")
        seen = []
        monkeypatch.setattr(mist_location, "refresh", lambda sid, mid=None: seen.append(sid) or {"ok": 1})
        res = mist_location.refresh_all()
        assert res["sites_refreshed"] == 2 and set(seen) == {"site-1", "site-2"}

    def test_refresh_all_best_effort(self, monkeypatch):
        MistSite.objects.create(mist_id="site-1", name="WH1")
        MistSite.objects.create(mist_id="site-2", name="WH2")

        def flaky(sid, mid=None):
            if sid == "site-1":
                raise RuntimeError("boom")
            return {"ok": 1}

        monkeypatch.setattr(mist_location, "refresh", flaky)
        res = mist_location.refresh_all()
        assert res["sites_refreshed"] == 1  # site-2 only; site-1 swallowed


# ── API: /api/wireless/location/ ─────────────────────────────────────────────
class TestLocationEndpoint:
    def test_requires_auth(self, api_client):
        assert api_client.get("/api/wireless/location/?site=site-1").status_code in (401, 403)

    def test_requires_site_param(self, auth_client):
        r = auth_client.get("/api/wireless/location/")
        assert r.status_code == 400

    def test_returns_cached_payload(self, auth_client, monkeypatch):
        monkeypatch.setattr(mist_location, "_mist_client", lambda: (FakeMist(), object()))
        mist_location.refresh("site-1")  # warm cache → key includes resolved map id
        r = auth_client.get("/api/wireless/location/?site=site-1&map=map-1")
        assert r.status_code == 200
        body = r.json()
        assert body["map"]["id"] == "map-1" and body["summary"]["aps_total"] == 2
        # The Mist token must never reach the browser.
        assert "Token " not in r.content.decode() and "Authorization" not in r.content.decode()

    def test_on_demand_refresh_fallback(self, auth_client, monkeypatch):
        # No map param, cold cache → view triggers refresh().
        monkeypatch.setattr(mist_location, "_mist_client", lambda: (FakeMist(), object()))
        r = auth_client.get("/api/wireless/location/?site=site-1")
        assert r.status_code == 200 and r.json()["map"]["id"] == "map-1"

    def test_not_configured_409(self, auth_client, monkeypatch):
        monkeypatch.setattr(mist_location, "_mist_client", lambda: (None, None))
        r = auth_client.get("/api/wireless/location/?site=site-1")
        assert r.status_code == 409

    def test_no_map_404(self, auth_client, monkeypatch):
        monkeypatch.setattr(mist_location, "_mist_client", lambda: (FakeMist(maps=[]), object()))
        r = auth_client.get("/api/wireless/location/?site=site-1")
        assert r.status_code == 404
