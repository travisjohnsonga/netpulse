from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


@pytest.fixture
def device():
    from apps.devices.models import Device
    return Device.objects.create(hostname="router1", ip_address="192.168.98.100", status="active")


# ── Endpoint ──────────────────────────────────────────────────────────────────

class TestCollectionStatusEndpoint:
    def test_requires_auth(self, api_client, device):
        assert api_client.get(f"/api/devices/{device.id}/collection-status/").status_code == 401

    def test_returns_status(self, auth_client, device, monkeypatch):
        from apps.devices import collection_status as cs
        sample = {
            "device_id": str(device.id),
            "gnmi": {"active": True, "last_seen_seconds_ago": 15,
                     "metrics_per_push": 294, "interval_seconds": 30},
            "snmp": {"active": False, "suppressed": True, "suppressed_reason": "gNMI active",
                     "last_poll_seconds_ago": 299, "interval_seconds": 300, "version": None},
            "primary": "gnmi", "any_active": True,
        }
        monkeypatch.setattr(cs, "build_collection_status", lambda dev: sample)
        resp = auth_client.get(f"/api/devices/{device.id}/collection-status/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["primary"] == "gnmi"
        assert body["gnmi"]["metrics_per_push"] == 294
        assert body["any_active"] is True


# ── build_collection_status logic ──────────────────────────────────────────────

class TestBuildCollectionStatus:
    def _patch_activity(self, monkeypatch, **activity):
        from apps.devices import collection_status as cs
        base = {"gnmi_last_seen": None, "snmp_last_seen": None, "gnmi_field_count": None}
        base.update(activity)
        monkeypatch.setattr(cs, "_query_activity", lambda device_id: base)

    def test_gnmi_active_recent(self, device, monkeypatch):
        from apps.devices import collection_status as cs
        now = timezone.now()
        self._patch_activity(monkeypatch,
                             gnmi_last_seen=now - timedelta(seconds=15),
                             gnmi_field_count=294)
        out = cs.build_collection_status(device, now=now)
        assert out["gnmi"]["active"] is True
        assert out["gnmi"]["last_seen_seconds_ago"] == 15
        assert out["gnmi"]["metrics_per_push"] == 294
        assert out["primary"] == "gnmi"
        assert out["any_active"] is True
        assert out["snmp"]["active"] is False

    def test_gnmi_stale_inactive(self, device, monkeypatch):
        from apps.devices import collection_status as cs
        now = timezone.now()
        # Older than GNMI_STALE_SECONDS (120s) → inactive, fields hidden.
        self._patch_activity(monkeypatch,
                             gnmi_last_seen=now - timedelta(seconds=200),
                             gnmi_field_count=294)
        out = cs.build_collection_status(device, now=now)
        assert out["gnmi"]["active"] is False
        assert out["gnmi"]["last_seen_seconds_ago"] is None
        assert out["gnmi"]["metrics_per_push"] is None
        assert out["primary"] is None
        assert out["any_active"] is False

    def test_snmp_active_and_primary_when_no_gnmi(self, device, monkeypatch):
        from apps.devices import collection_status as cs
        now = timezone.now()
        self._patch_activity(monkeypatch, snmp_last_seen=now - timedelta(seconds=120))
        out = cs.build_collection_status(device, now=now)
        assert out["snmp"]["active"] is True
        assert out["snmp"]["last_poll_seconds_ago"] == 120
        assert out["primary"] == "snmp"
        assert out["any_active"] is True

    def test_gnmi_active_suppresses_snmp(self, device, monkeypatch):
        # Adaptive polling: when gNMI is streaming, SNMP is suppressed even
        # though it polled recently (right before suppression kicked in).
        from apps.devices import collection_status as cs
        now = timezone.now()
        self._patch_activity(monkeypatch,
                             gnmi_last_seen=now - timedelta(seconds=10),
                             gnmi_field_count=100,
                             snmp_last_seen=now - timedelta(seconds=299))
        out = cs.build_collection_status(device, now=now)
        assert out["primary"] == "gnmi"
        assert out["gnmi"]["active"] is True
        assert out["snmp"]["active"] is False
        assert out["snmp"]["suppressed"] is True
        assert out["snmp"]["suppressed_reason"] == "gNMI active"
        # Last poll age still reported while suppressed.
        assert out["snmp"]["last_poll_seconds_ago"] == 299

    def test_snmp_not_suppressed_when_no_gnmi(self, device, monkeypatch):
        from apps.devices import collection_status as cs
        now = timezone.now()
        self._patch_activity(monkeypatch, snmp_last_seen=now - timedelta(seconds=30))
        out = cs.build_collection_status(device, now=now)
        assert out["snmp"]["active"] is True
        assert out["snmp"]["suppressed"] is False
        assert "suppressed_reason" not in out["snmp"]

    def test_no_telemetry(self, device, monkeypatch):
        from apps.devices import collection_status as cs
        self._patch_activity(monkeypatch)  # all None
        out = cs.build_collection_status(device, now=timezone.now())
        assert out["any_active"] is False
        assert out["primary"] is None
        assert out["gnmi"]["active"] is False and out["snmp"]["active"] is False

    def test_intervals_from_telemetry_config(self, device, monkeypatch):
        from apps.devices import collection_status as cs
        from apps.telemetry.models import TelemetryConfig
        TelemetryConfig.objects.create(device=device, gnmi_interval=10, snmp_interval=600)
        device.refresh_from_db()
        self._patch_activity(monkeypatch)
        out = cs.build_collection_status(device, now=timezone.now())
        assert out["gnmi"]["interval_seconds"] == 10
        assert out["snmp"]["interval_seconds"] == 600

    def test_snmp_interval_override(self, device, monkeypatch):
        from apps.devices import collection_status as cs
        from apps.telemetry.models import TelemetryConfig
        TelemetryConfig.objects.create(
            device=device, override_intervals=True, device_metrics_interval=30, snmp_interval=300)
        device.refresh_from_db()
        self._patch_activity(monkeypatch)
        out = cs.build_collection_status(device, now=timezone.now())
        assert out["snmp"]["interval_seconds"] == 30

    def test_snmp_version_from_credential_profile(self, device, monkeypatch):
        from apps.credentials.models import CredentialProfile
        from apps.devices import collection_status as cs
        prof = CredentialProfile.objects.create(name="v3prof", snmpv3_enabled=True)
        device.credential_profile = prof
        device.save()
        device.refresh_from_db()
        self._patch_activity(monkeypatch)
        out = cs.build_collection_status(device, now=timezone.now())
        assert out["snmp"]["version"] == "v3"


class TestQueryActivityDegrades:
    def test_influx_down_returns_all_none(self, monkeypatch):
        from apps.devices import collection_status as cs
        from apps.devices import metrics_influx
        monkeypatch.setattr(metrics_influx, "_client",
                            lambda: (_ for _ in ()).throw(RuntimeError("down")))
        out = cs._query_activity("3")
        assert out == {"gnmi_last_seen": None, "snmp_last_seen": None, "gnmi_field_count": None}
