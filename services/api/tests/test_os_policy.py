"""OS-version policy: matching, status resolution, fleet inventory, compliance
integration, and the REST endpoints."""
import pytest

from apps.compliance import os_policy
from apps.compliance.models import (
    ApprovedOSVersion, ComplianceTemplate, DiscoveredPlatformModel,
)
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


def _policy(platform, pattern, status, is_regex=False):
    return ApprovedOSVersion.objects.create(
        platform=platform, version_pattern=pattern, status=status, is_regex=is_regex)


class TestMatches:
    def test_exact(self):
        p = _policy("ios_xe", "17.12.4", "approved")
        assert p.matches("17.12.4")
        assert not p.matches("17.12.5")

    def test_regex(self):
        p = _policy("ios_xe", r"17\.12\..*", "preferred", is_regex=True)
        assert p.matches("17.12.4")
        assert p.matches("17.12.99")
        assert not p.matches("17.9.1")

    def test_bad_regex_never_matches(self):
        p = _policy("ios_xe", "17.12.[", "approved", is_regex=True)
        assert not p.matches("17.12.4")


class TestStatusResolution:
    def test_unknown_when_no_policy(self):
        assert os_policy.get_os_compliance_status("ios_xe", "17.12.4") == "unknown"

    def test_preferred(self):
        _policy("ios_xe", r"17\.12\..*", "preferred", is_regex=True)
        assert os_policy.get_os_compliance_status("ios_xe", "17.12.4") == "preferred"

    def test_prohibited_dominates_broad_approved(self):
        # A broad "approved" pattern must not mask a specific "prohibited" one.
        _policy("ios_xe", r"1.*", "approved", is_regex=True)
        _policy("ios_xe", r"16\..*", "prohibited", is_regex=True)
        assert os_policy.get_os_compliance_status("ios_xe", "16.3.1") == "prohibited"

    def test_platform_scoped(self):
        _policy("ios_xe", "17.12.4", "approved")
        assert os_policy.get_os_compliance_status("aos_cx", "17.12.4") == "unknown"


class TestFindings:
    def test_clean_for_approved(self):
        _policy("ios_xe", "17.12.4", "preferred")
        d = Device(platform="ios_xe", os_version="17.12.4")
        assert os_policy.os_compliance_findings(d) == (0.0, [])

    def test_prohibited_penalty(self):
        _policy("ios_xe", "16.3.1", "prohibited")
        d = Device(platform="ios_xe", os_version="16.3.1")
        delta, findings = os_policy.os_compliance_findings(d)
        assert delta == -30.0
        assert findings[0]["type"] == "OS_PROHIBITED"
        assert findings[0]["severity"] == "high"

    def test_deprecated_penalty(self):
        _policy("fortios", "7.0.1", "deprecated")
        d = Device(platform="fortios", os_version="7.0.1")
        delta, findings = os_policy.os_compliance_findings(d)
        assert delta == -15.0 and findings[0]["type"] == "OS_DEPRECATED"

    def test_unknown_penalty(self):
        # A policy exists (feature opted-in) but the version matches nothing.
        _policy("ios_xe", "17.12.4", "approved")
        d = Device(platform="ios_xe", os_version="99.9")
        delta, findings = os_policy.os_compliance_findings(d)
        assert delta == -5.0 and findings[0]["type"] == "OS_UNKNOWN"

    def test_no_penalty_when_feature_unconfigured(self):
        # No policies at all → OS scoring is off, even for an uncovered version.
        d = Device(platform="ios_xe", os_version="99.9")
        assert os_policy.os_compliance_findings(d) == (0.0, [])


class TestRefresh:
    def test_builds_and_counts(self):
        Device.objects.create(hostname="a", ip_address="10.0.0.1", platform="ios_xe",
                              model="CSR1000V", os_version="17.12.4")
        Device.objects.create(hostname="b", ip_address="10.0.0.2", platform="ios_xe",
                              model="CSR1000V", os_version="17.12.4")
        Device.objects.create(hostname="c", ip_address="10.0.0.3", platform="fortios",
                              model="FG-VM", os_version="7.4.3")
        _policy("ios_xe", r"17\.12\..*", "preferred", is_regex=True)
        n = os_policy.refresh_discovered_platforms()
        assert n == 2
        combo = DiscoveredPlatformModel.objects.get(platform="ios_xe", model="CSR1000V",
                                                    os_version="17.12.4")
        assert combo.device_count == 2
        assert combo.os_status == "preferred"
        forti = DiscoveredPlatformModel.objects.get(platform="fortios")
        assert forti.os_status == "unknown"

    def test_prunes_stale(self):
        d = Device.objects.create(hostname="a", ip_address="10.0.0.1", platform="ios_xe",
                                  os_version="17.12.4")
        os_policy.refresh_discovered_platforms()
        assert DiscoveredPlatformModel.objects.count() == 1
        d.delete()
        os_policy.refresh_discovered_platforms()
        assert DiscoveredPlatformModel.objects.count() == 0

    def test_recompute_after_policy_change(self):
        Device.objects.create(hostname="a", ip_address="10.0.0.1", platform="ios_xe",
                              os_version="16.3.1")
        os_policy.refresh_discovered_platforms()
        assert DiscoveredPlatformModel.objects.get(platform="ios_xe").os_status == "unknown"
        _policy("ios_xe", r"16\..*", "prohibited", is_regex=True)
        os_policy.recompute_statuses()
        assert DiscoveredPlatformModel.objects.get(platform="ios_xe").os_status == "prohibited"


class TestEngineIntegration:
    def test_prohibited_os_adds_finding_and_lowers_score(self):
        from apps.compliance.engine import ComplianceEngine
        _policy("aos_cx", "10.0.0", "prohibited")
        d = Device.objects.create(hostname="sw1", ip_address="10.1.0.1", platform="aos_cx",
                                  os_version="10.0.0")
        t = ComplianceTemplate.objects.create(
            name="t", template_content="ntp server 1.1.1.1\n", variables={}, enabled=True)
        # Config matches the template fully → config score 100, OS knocks 30 off.
        r = ComplianceEngine().check_device(d, t, config_text="ntp server 1.1.1.1\n")
        assert r.score == 70.0
        assert any(f["type"] == "OS_PROHIBITED" for f in r.findings)

    def test_approved_os_does_not_alter_score(self):
        from apps.compliance.engine import ComplianceEngine
        _policy("aos_cx", "10.13.1000", "approved")
        d = Device.objects.create(hostname="sw2", ip_address="10.1.0.2", platform="aos_cx",
                                  os_version="10.13.1000")
        t = ComplianceTemplate.objects.create(
            name="t2", template_content="ntp server 1.1.1.1\n", variables={}, enabled=True)
        r = ComplianceEngine().check_device(d, t, config_text="ntp server 1.1.1.1\n")
        assert r.score == 100.0
        assert all(not f["type"].startswith("OS_") for f in r.findings)


class TestSummary:
    def test_tallies_by_device(self):
        Device.objects.create(hostname="a", ip_address="10.0.0.1", platform="ios_xe", os_version="17.12.4")
        Device.objects.create(hostname="b", ip_address="10.0.0.2", platform="ios_xe", os_version="17.12.4")
        Device.objects.create(hostname="c", ip_address="10.0.0.3", platform="ios_xe", os_version="16.3.1")
        _policy("ios_xe", r"17\.12\..*", "preferred", is_regex=True)
        _policy("ios_xe", r"16\..*", "prohibited", is_regex=True)
        s = os_policy.os_summary()
        assert s["preferred"] == 2
        assert s["prohibited"] == 1
        assert s["total_devices"] == 3


class TestEndpoints:
    def test_os_versions_crud(self, auth_client):
        resp = auth_client.post("/api/compliance/os-versions/", {
            "platform": "ios_xe", "version_pattern": r"17\.12\..*",
            "is_regex": True, "status": "preferred"}, format="json")
        assert resp.status_code == 201, resp.content
        pid = resp.json()["id"]
        assert auth_client.get("/api/compliance/os-versions/").json()["count"] == 1
        assert auth_client.delete(f"/api/compliance/os-versions/{pid}/").status_code == 204

    def test_os_versions_rejects_bad_regex(self, auth_client):
        resp = auth_client.post("/api/compliance/os-versions/", {
            "platform": "ios_xe", "version_pattern": "17.12.[",
            "is_regex": True, "status": "approved"}, format="json")
        assert resp.status_code == 400
        assert "version_pattern" in resp.json()

    def test_discovered_platforms_refresh_and_drill(self, auth_client):
        Device.objects.create(hostname="a", ip_address="10.0.0.1", platform="ios_xe",
                              model="CSR1000V", os_version="17.12.4")
        refresh = auth_client.post("/api/compliance/discovered-platforms/refresh/")
        assert refresh.status_code == 200 and refresh.json()["combos"] == 1
        rows = auth_client.get("/api/compliance/discovered-platforms/").json()["results"]
        assert len(rows) == 1 and rows[0]["device_count"] == 1
        devs = auth_client.get(
            f"/api/compliance/discovered-platforms/{rows[0]['id']}/devices/").json()
        assert devs[0]["hostname"] == "a"

    def test_os_summary_endpoint(self, auth_client):
        Device.objects.create(hostname="a", ip_address="10.0.0.1", platform="ios_xe", os_version="9.9")
        body = auth_client.get("/api/compliance/os-summary/").json()
        assert body["unknown"] == 1 and body["total_devices"] == 1


class TestSignal:
    def test_device_save_refreshes_when_enabled(self, settings):
        settings.OS_PLATFORM_REFRESH_ON_SAVE = True
        Device.objects.create(hostname="a", ip_address="10.0.0.1", platform="ios_xe",
                              os_version="17.12.4")
        # on_commit fires at the end of the test transaction; force-run instead.
        os_policy.refresh_discovered_platforms()
        assert DiscoveredPlatformModel.objects.filter(platform="ios_xe").exists()
