"""
ConfigCollectionLog — the per-attempt config-collection audit trail.

Covers the model, the collect_one() instrumentation (one row written on every
outcome, with status/method/duration/bytes), and the collection-log /
collection-stats / per-device endpoints.
"""
import pytest
from django.utils import timezone

from apps.compliance import collector
from apps.configbackup.models import ConfigCollectionLog
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    return Device.objects.create(hostname="cl-rtr", ip_address="10.7.0.1", status="active")


# ── collect_one() writes a log on every outcome ─────────────────────────────────

class TestCollectOneLogging:
    def test_success_logs_success_row(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "hostname cl-rtr\n!")
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)

        res = collector.collect_one(device, collected_by="manual")

        assert res["ok"] is True
        log = ConfigCollectionLog.objects.get(device=device)
        assert log.status == ConfigCollectionLog.Status.SUCCESS
        assert log.collected_by == "manual"
        assert log.config_changed is False        # initial baseline
        assert log.bytes_collected == len("hostname cl-rtr\n!")
        assert log.duration_ms is not None and log.duration_ms >= 0

    def test_unchanged_logs_unchanged_row(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "same config\n!")
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)

        collector.collect_one(device)                     # first store
        collector.collect_one(device)                     # identical → unchanged

        logs = list(ConfigCollectionLog.objects.filter(device=device).order_by("collected_at"))
        assert len(logs) == 2
        assert logs[0].status == ConfigCollectionLog.Status.SUCCESS
        assert logs[1].status == ConfigCollectionLog.Status.UNCHANGED
        assert logs[1].config_changed is False

    def test_timeout_logs_timeout_row(self, device, monkeypatch):
        def _boom(d, c):
            raise TimeoutError("connection timed out")
        monkeypatch.setattr(collector, "_fetch_running_config", _boom)

        res = collector.collect_one(device)

        assert res == {"ok": False, "error": "timeout"}
        log = ConfigCollectionLog.objects.get(device=device)
        assert log.status == ConfigCollectionLog.Status.TIMEOUT
        assert log.config_changed is None                 # never reached
        assert "timed out" in log.error_message

    def test_auth_failure_logs_auth_failed_row(self, device, monkeypatch):
        class AuthenticationException(Exception):
            pass

        def _boom(d, c):
            raise AuthenticationException("bad creds")
        monkeypatch.setattr(collector, "_fetch_running_config", _boom)

        res = collector.collect_one(device)

        assert res == {"ok": False, "error": "auth_failed"}
        assert ConfigCollectionLog.objects.get(device=device).status == \
            ConfigCollectionLog.Status.AUTH_FAILED

    def test_empty_logs_empty_row(self, device, monkeypatch):
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "   \n  ")

        res = collector.collect_one(device)

        assert res == {"ok": False, "error": "empty"}
        assert ConfigCollectionLog.objects.get(device=device).status == \
            ConfigCollectionLog.Status.EMPTY

    def test_method_recorded_for_real_dispatch(self, device, monkeypatch):
        """The dispatcher records the transport; here SSH/Netmiko path."""
        device.platform = "ios"
        device.save()
        monkeypatch.setattr(collector, "_fetch_via_ssh", lambda d, p, c: "hostname x\n!")
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)

        collector.collect_one(device)

        assert ConfigCollectionLog.objects.get(device=device).method == "netmiko"


# ── endpoints ───────────────────────────────────────────────────────────────────

class TestCollectionLogEndpoints:
    def _mk(self, device, status, **kw):
        return ConfigCollectionLog.objects.create(device=device, status=status, **kw)

    def test_collection_log_list_and_filters(self, device, auth_client):
        other = Device.objects.create(hostname="cl-other", ip_address="10.7.0.2")
        self._mk(device, ConfigCollectionLog.Status.SUCCESS, method="ssh")
        self._mk(device, ConfigCollectionLog.Status.TIMEOUT)
        self._mk(other, ConfigCollectionLog.Status.SUCCESS)

        resp = auth_client.get("/api/configbackup/collection-log/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 3

        # filter by device
        resp = auth_client.get(f"/api/configbackup/collection-log/?device_id={device.id}")
        assert resp.json()["count"] == 2

        # filter by status
        resp = auth_client.get("/api/configbackup/collection-log/?status=timeout")
        assert resp.json()["count"] == 1
        assert resp.json()["results"][0]["status"] == "timeout"

    def test_collection_stats(self, device, auth_client):
        never = Device.objects.create(hostname="never", ip_address="10.7.0.9", status="active")
        self._mk(device, ConfigCollectionLog.Status.SUCCESS)
        self._mk(device, ConfigCollectionLog.Status.UNCHANGED)
        self._mk(device, ConfigCollectionLog.Status.TIMEOUT)   # most recent → failing

        resp = auth_client.get("/api/configbackup/collection-stats/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_24h"]["total"] == 3
        assert body["last_24h"]["success"] == 2                # success + unchanged
        assert body["last_24h"]["unchanged"] == 1
        assert body["last_24h"]["failed"] == 1
        assert body["last_24h"]["success_rate"] == pytest.approx(66.7, abs=0.1)
        # `never` device is active with no logs
        assert body["devices_never_collected"] >= 1
        failing = {d["hostname"]: d for d in body["devices_failing"]}
        assert "cl-rtr" in failing
        assert failing["cl-rtr"]["consecutive_failures"] == 1
        assert failing["cl-rtr"]["last_error"] == "timeout"
        assert never.hostname not in failing

    def test_per_device_collection_log(self, device, auth_client):
        self._mk(device, ConfigCollectionLog.Status.SUCCESS)
        self._mk(device, ConfigCollectionLog.Status.UNCHANGED)

        resp = auth_client.get(f"/api/devices/{device.id}/collection-log/")
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        assert rows[0]["device_hostname"] == "cl-rtr"


# ── Unsupported platforms are skipped (UniFi/Mist/cloud-managed) ─────────────────

class TestUnsupportedPlatformSkip:
    def test_collect_one_skips_wireless_ap(self, monkeypatch):
        ap = Device.objects.create(hostname="wco2-wh-ap-08", ip_address="10.16.1.8",
                                   status="active", platform="unifi_ap")
        # Should never attempt a fetch and must write NO collection-log row.
        called = {"fetch": False}
        monkeypatch.setattr(collector, "_fetch_running_config",
                            lambda d, c: called.__setitem__("fetch", True) or "x")
        res = collector.collect_one(ap, collected_by="manual")
        assert res == {"ok": False, "error": "not_supported", "skipped": True}
        assert called["fetch"] is False
        assert not ConfigCollectionLog.objects.filter(device=ap).exists()

    def test_collect_one_skips_blank_platform(self):
        d = Device.objects.create(hostname="mystery", ip_address="10.0.9.9",
                                  status="active", platform="")
        res = collector.collect_one(d)
        assert res.get("skipped") is True
        assert not ConfigCollectionLog.objects.filter(device=d).exists()

    def test_collect_one_runs_for_supported_platform(self, monkeypatch):
        d = Device.objects.create(hostname="rtr", ip_address="10.0.0.7",
                                  status="active", platform="ios_xe")
        monkeypatch.setattr(collector, "_fetch_running_config", lambda d, c: "hostname rtr\n!")
        monkeypatch.setattr(collector, "publish_collected", lambda did: None)
        res = collector.collect_one(d)
        assert res["ok"] is True
        assert ConfigCollectionLog.objects.filter(device=d).exists()

    def test_collect_all_excludes_unsupported(self, monkeypatch):
        from apps.configbackup import tasks
        Device.objects.create(hostname="ap1", ip_address="10.0.1.1", status="active", platform="mist_ap")
        Device.objects.create(hostname="udm1", ip_address="10.0.1.2", status="active", platform="unifi_udm")
        Device.objects.create(hostname="sw1", ip_address="10.0.1.3", status="active", platform="ios")
        seen = []
        monkeypatch.setattr(collector, "collect_one",
                            lambda d, collected_by="scheduled": seen.append(d.hostname) or {"ok": True})
        res = tasks.collect_all_configs()
        # Only the supported device is iterated.
        assert seen == ["sw1"] and res["total"] == 1


class TestInfraHostname:
    def test_docker_container_names_flagged(self):
        from apps.devices.management.commands.run_discovery import is_infra_hostname
        assert is_infra_hostname("netpulse-frontend-1.netpulse_netpulse-net")
        assert is_infra_hostname("netpulse-api-1")
        assert is_infra_hostname("spane-scheduler-1")
        assert is_infra_hostname("myhost-ingest-snmp-1")

    def test_real_hostnames_not_flagged(self):
        from apps.devices.management.commands.run_discovery import is_infra_hostname
        assert not is_infra_hostname("wco2-mdf-crt-01")
        assert not is_infra_hostname("router1.dnstest.local")
        assert not is_infra_hostname("")
        assert not is_infra_hostname("core-sw-01")
