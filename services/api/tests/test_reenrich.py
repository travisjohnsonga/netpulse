"""Daily + on-demand fleet identity re-enrichment (apps.devices.reenrich)."""
import datetime

import pytest
from django.core.cache import cache

from apps.credentials.models import CredentialProfile
from apps.devices import enrich, reenrich
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def profile():
    return CredentialProfile.objects.create(name="p", snmpv3_enabled=True, snmpv3_username="u")


def _device(profile, host, ip):
    return Device.objects.create(hostname=host, ip_address=ip, management_ip=ip,
                                 platform="ios", credential_profile=profile,
                                 status=Device.Status.ACTIVE)


class TestIdentityRefreshScope:
    """refresh_device_identity refreshes identity ONLY — no interfaces/LLDP/config."""

    def test_identity_only_no_discovery(self, profile, monkeypatch):
        dev = _device(profile, "rtr", "10.0.0.1")
        monkeypatch.setattr(enrich, "_snmp_collect", lambda ip, p, s: {
            enrich._OID_SYS_DESCR: "Cisco IOS-XE Software, C8000V Software, Version 17.12.4",
            enrich._OID_SYS_OBJID: "1.3.6.1.4.1.9.1.2862",
        })
        monkeypatch.setattr(enrich, "_ssh_collect", lambda ip, p, s: {})
        # These MUST NOT be called by the lightweight path.
        called = {"iface": False, "lldp": False, "config": False, "hostname": False}
        monkeypatch.setattr(enrich, "_discover_interfaces",
                            lambda d: called.__setitem__("iface", True) or ([], 0, 0))
        monkeypatch.setattr(enrich, "_discover_lldp",
                            lambda d, i=None: called.__setitem__("lldp", True) or 0)
        monkeypatch.setattr(enrich, "_collect_config",
                            lambda d: called.__setitem__("config", True))

        detail: dict = {}
        changed = enrich.refresh_device_identity(dev.id, result=detail)
        dev.refresh_from_db()
        assert dev.os_version == "17.12.4"
        assert set(changed) >= {"os_version", "platform"}
        assert detail["changed"] == changed
        assert not any(called.values())   # zero discovery/config work

    def test_no_profile_records_error(self, monkeypatch):
        dev = Device.objects.create(hostname="np", ip_address="10.0.0.9",
                                    status=Device.Status.ACTIVE)
        detail: dict = {}
        assert enrich.refresh_device_identity(dev.id, result=detail) == {}
        assert any(e["step"] == "device-info" for e in detail["errors"])


class TestStaggerMath:
    def test_zero_for_single_device(self):
        assert reenrich._per_device_delay(0) == 0.0
        assert reenrich._per_device_delay(1) == 0.0

    def test_spreads_small_fleet_but_caps(self, monkeypatch):
        monkeypatch.setenv("REENRICH_STAGGER_WINDOW_S", "3600")
        monkeypatch.setenv("REENRICH_MAX_STAGGER_S", "30")
        # 3600/2 = 1800 → capped to 30.
        assert reenrich._per_device_delay(2) == 30.0

    def test_large_fleet_self_throttles_under_cap(self, monkeypatch):
        monkeypatch.setenv("REENRICH_STAGGER_WINDOW_S", "3600")
        monkeypatch.setenv("REENRICH_MAX_STAGGER_S", "30")
        # 3600/1000 = 3.6 → under the cap, used directly.
        assert abs(reenrich._per_device_delay(1000) - 3.6) < 1e-6

    def test_window_zero_disables_delay(self, monkeypatch):
        monkeypatch.setenv("REENRICH_STAGGER_WINDOW_S", "0")
        assert reenrich._per_device_delay(50) == 0.0


class TestRunWorker:
    def test_partial_failure_does_not_abort(self, profile, monkeypatch):
        good = _device(profile, "good-1", "10.0.0.1")
        changer = _device(profile, "changed-1", "10.0.0.2")
        bad = _device(profile, "bad-1", "10.0.0.3")
        down = _device(profile, "down-1", "10.0.0.4")
        monkeypatch.setattr(reenrich.time, "sleep", lambda *_: None)  # no real staggering

        def fake_refresh(device_id, result=None):
            result = result if result is not None else {}
            result.setdefault("errors", [])
            result.setdefault("changed", {})
            result["reachable"] = True
            if device_id == changer.id:
                result["changed"] = {"os_version": "99.9"}
            elif device_id == bad.id:
                result["errors"].append({"step": "device-info", "message": "creds"})
            elif device_id == down.id:
                result["reachable"] = False   # collectors returned no data
            return result["changed"]
        monkeypatch.setattr(enrich, "refresh_device_identity", fake_refresh)

        reenrich._run_worker(None, trigger="manual", ttl=600)
        st = reenrich.get_status()
        assert st["running"] is False
        assert st["total"] == 4
        assert st["done"] == 4
        assert st["success"] == 2          # good + changer
        assert st["failed"] == 2           # bad (error) + down (unreachable)
        assert st["unreachable"] == 1      # down only
        assert st["updated"] == 1          # only changer had field changes
        assert st["finished_at"] is not None
        assert {c["device"] for c in st["changes"]} == {"changed-1"}
        err_by_host = {e["device"]: e["error"] for e in st["errors"]}
        # Generic messages only — no exception text leaks.
        assert err_by_host["bad-1"] == "identity refresh failed"
        assert err_by_host["down-1"] == "unreachable — no response"
        # Lock released.
        assert cache.get(reenrich._LOCK_KEY) is None

    def test_skips_devices_without_credentials(self, profile, monkeypatch):
        _device(profile, "with-cred", "10.0.0.1")
        Device.objects.create(hostname="no-cred", ip_address="10.0.0.2",
                              status=Device.Status.ACTIVE)  # no credential_profile
        monkeypatch.setattr(reenrich.time, "sleep", lambda *_: None)
        seen = []
        monkeypatch.setattr(enrich, "refresh_device_identity",
                            lambda did, result=None: seen.append(did) or {})
        reenrich._run_worker(None, trigger="manual", ttl=600)
        assert len(seen) == 1              # only the credentialed device
        assert reenrich.get_status()["total"] == 1


class TestStartReenrichAll:
    def test_lock_prevents_overlap(self, profile):
        cache.add(reenrich._LOCK_KEY, True, timeout=600)   # simulate an active run
        started, _ = reenrich.start_reenrich_all(trigger="manual")
        assert started is False

    def test_starts_when_free(self, profile, monkeypatch):
        _device(profile, "d1", "10.0.0.1")
        # Don't spawn a real thread — assert it would have been started.
        launched = {}
        class _FakeThread:
            def __init__(self, *a, **k): launched["k"] = k
            def start(self): launched["started"] = True
        monkeypatch.setattr(reenrich.threading, "Thread", _FakeThread)
        started, status = reenrich.start_reenrich_all(trigger="manual")
        assert started is True
        assert status["running"] is True
        assert status["total"] == 1
        assert launched.get("started") is True
        assert cache.get(reenrich._LOCK_KEY) is True   # held for the worker


class TestScheduledDueCheck:
    def _now(self, hour):
        return datetime.datetime(2026, 8, 2, hour, 0, tzinfo=datetime.timezone.utc)

    def test_not_due_wrong_hour(self, monkeypatch):
        monkeypatch.setenv("REENRICH_ENABLED", "true")
        monkeypatch.setenv("REENRICH_RUN_HOUR", "4")
        assert reenrich.run_due_reenrichment(now=self._now(5)) is False

    def test_disabled_never_runs(self, monkeypatch):
        monkeypatch.setenv("REENRICH_ENABLED", "false")
        monkeypatch.setenv("REENRICH_RUN_HOUR", "4")
        assert reenrich.run_due_reenrichment(now=self._now(4)) is False

    def test_due_starts_and_dedupes_same_day(self, monkeypatch):
        monkeypatch.setenv("REENRICH_ENABLED", "true")
        monkeypatch.setenv("REENRICH_RUN_HOUR", "4")
        calls = []
        monkeypatch.setattr(reenrich, "start_reenrich_all",
                            lambda trigger="scheduled": calls.append(trigger) or (True, {}))
        assert reenrich.run_due_reenrichment(now=self._now(4)) is True
        assert calls == ["scheduled"]
        # Same day, same hour → deduped (already marked done).
        assert reenrich.run_due_reenrichment(now=self._now(4)) is False
        assert calls == ["scheduled"]


class TestReenrichAllEndpoint:
    def test_requires_auth(self, api_client):
        assert api_client.post("/api/devices/reenrich-all/").status_code == 401

    def test_post_starts_and_get_returns_status(self, auth_client, monkeypatch):
        monkeypatch.setattr(reenrich, "start_reenrich_all",
                            lambda ids=None, trigger="manual": (True, {"running": True, "total": 3}))
        resp = auth_client.post("/api/devices/reenrich-all/")
        assert resp.status_code == 202
        assert resp.json()["total"] == 3

        monkeypatch.setattr(reenrich, "get_status",
                            lambda: {"running": False, "success": 3, "failed": 0})
        s = auth_client.get("/api/devices/reenrich-all/")
        assert s.status_code == 200
        assert s.json()["success"] == 3

    def test_conflict_when_running(self, auth_client, monkeypatch):
        monkeypatch.setattr(reenrich, "start_reenrich_all",
                            lambda ids=None, trigger="manual": (False, {"running": True}))
        resp = auth_client.post("/api/devices/reenrich-all/")
        assert resp.status_code == 409

    def test_bad_device_ids(self, auth_client):
        resp = auth_client.post("/api/devices/reenrich-all/",
                                {"device_ids": "nope"}, format="json")
        assert resp.status_code == 400
