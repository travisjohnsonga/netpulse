"""Seeding OS-version policy placeholders from inventory + new-version detection."""
import pytest

from apps.compliance import os_policy
from apps.compliance.models import ApprovedOSVersion
from apps.devices.models import Device

pytestmark = pytest.mark.django_db

UNKNOWN = ApprovedOSVersion.Status.UNKNOWN


def _device(host, ip, platform="ios_xe", version="17.12.4"):
    return Device.objects.create(hostname=host, ip_address=ip, platform=platform, os_version=version)


class TestSeed:
    def test_seeds_placeholders(self):
        _device("a", "10.0.0.1", "ios_xe", "17.12.4")
        _device("b", "10.0.0.2", "ios_xe", "17.12.4")  # same combo
        _device("c", "10.0.0.3", "fortios", "7.4.3")
        res = os_policy.seed_os_versions_from_inventory()
        assert res["created"] == 2 and res["devices"] == 3
        rows = {(r.platform, r.version_pattern): r for r in ApprovedOSVersion.objects.all()}
        assert set(rows) == {("ios_xe", "17.12.4"), ("fortios", "7.4.3")}
        assert all(r.status == UNKNOWN and not r.is_regex for r in rows.values())

    def test_idempotent_and_preserves_user_status(self):
        _device("a", "10.0.0.1", "ios_xe", "17.12.4")
        os_policy.seed_os_versions_from_inventory()
        # Admin sets a real status.
        row = ApprovedOSVersion.objects.get(platform="ios_xe", version_pattern="17.12.4")
        row.status = "preferred"; row.save()
        # Re-seed must not clobber it or duplicate.
        res = os_policy.seed_os_versions_from_inventory()
        assert res["created"] == 0 and res["already_existed"] == 1
        row.refresh_from_db()
        assert row.status == "preferred"

    def test_skips_blank_versions(self):
        _device("a", "10.0.0.1", "ios_xe", "")
        Device.objects.create(hostname="b", ip_address="10.0.0.2", platform="ios_xe")  # null version
        assert os_policy.seed_os_versions_from_inventory()["created"] == 0


class TestPlaceholdersDontScore:
    def test_unknown_status_does_not_match_or_penalize(self):
        # A seeded 'unknown' placeholder must not resolve a status nor penalise.
        ApprovedOSVersion.objects.create(platform="ios_xe", version_pattern="17.12.4", status=UNKNOWN)
        assert os_policy.get_os_compliance_status("ios_xe", "17.12.4") == "unknown"
        d = Device(platform="ios_xe", os_version="17.12.4")
        assert os_policy.os_compliance_findings(d) == (0.0, [])

    def test_real_policy_activates_scoring(self):
        ApprovedOSVersion.objects.create(platform="ios_xe", version_pattern="17.12.4", status=UNKNOWN)
        ApprovedOSVersion.objects.create(platform="ios_xe", version_pattern="16.3.1", status="prohibited")
        # Now a real policy exists → an uncovered version draws the unknown nudge.
        d = Device(platform="ios_xe", os_version="99.9")
        delta, findings = os_policy.os_compliance_findings(d)
        assert delta == -5.0
        # And the prohibited one scores.
        d2 = Device(platform="ios_xe", os_version="16.3.1")
        assert os_policy.os_compliance_findings(d2)[0] == -30.0
        # The 'unknown' placeholder for 17.12.4 still doesn't match.
        assert os_policy.get_os_compliance_status("ios_xe", "17.12.4") == "unknown"


class TestNewVersionDetection:
    def test_note_new_version_seeds_and_alerts(self):
        from apps.alerts.models import AlertEvent
        d = _device("rtr1", "10.0.0.1", "ios_xe", "17.15.1")
        created = os_policy.note_new_os_version(d)
        assert created is True
        assert ApprovedOSVersion.objects.filter(platform="ios_xe", version_pattern="17.15.1",
                                                status=UNKNOWN).exists()
        ev = AlertEvent.objects.filter(labels__alert_type="new_os_version_detected").first()
        assert ev is not None and ev.annotations["severity"] == "info"

    def test_note_new_version_idempotent(self):
        d = _device("rtr1", "10.0.0.1", "ios_xe", "17.15.1")
        assert os_policy.note_new_os_version(d) is True
        assert os_policy.note_new_os_version(d) is False


class TestEndpoint:
    def test_sync_from_inventory(self, auth_client):
        _device("a", "10.0.0.1", "ios_xe", "17.12.4")
        _device("b", "10.0.0.2", "aos_cx", "10.13.1000")
        resp = auth_client.post("/api/compliance/os-versions/sync-from-inventory/")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["created"] == 2
        assert "2 device(s)" in body["message"]
        assert ApprovedOSVersion.objects.count() == 2
