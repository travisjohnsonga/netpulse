"""Per-collector config-DOWN bundle (build_config)."""
import pytest

from apps.checks.models import ServiceCheck
from apps.collectors.collector_config import build_config
from apps.collectors.models import Collector
from apps.credentials.models import CredentialProfile
from apps.devices.models import Device, Site

pytestmark = pytest.mark.django_db


@pytest.fixture
def snmp_profile():
    return CredentialProfile.objects.create(name="snmp", snmpv2c_enabled=True, vault_path="netpulse/credentials/1")


def _device(host, ip, profile, site=None, collector=None, status="active"):
    return Device.objects.create(
        hostname=host, ip_address=ip, platform="ios_xe", status=status,
        credential_profile=profile, site=site, collector=collector)


@pytest.fixture
def collector():
    return Collector.objects.create(name="edge", collector_type="remote", api_key_hash="k1",
                                    nats_account="collector-x")


class TestBuildConfig:
    def test_includes_assigned_site_devices(self, collector, snmp_profile):
        site = Site.objects.create(name="DC-A", default_collector=collector)
        _device("in-site", "10.0.0.1", snmp_profile, site=site)
        _device("other", "10.0.0.2", snmp_profile)  # no site, not pinned → excluded

        cfg = build_config(collector)
        hosts = {d["hostname"] for d in cfg["devices"]}
        assert hosts == {"in-site"}
        assert cfg["collector_id"] == collector.id
        assert cfg["nats_account"] == "collector-x"
        assert cfg["devices"][0]["cred_path"] == "netpulse/credentials/1"
        # No secret material in the bundle — only the vault path reference.
        assert "ssh_password" not in cfg["devices"][0]

    def test_includes_directly_pinned_device(self, collector, snmp_profile):
        _device("pinned", "10.0.0.3", snmp_profile, collector=collector)
        cfg = build_config(collector)
        assert {d["hostname"] for d in cfg["devices"]} == {"pinned"}

    def test_excludes_inactive_devices(self, collector, snmp_profile):
        site = Site.objects.create(name="DC-B", default_collector=collector)
        _device("down", "10.0.0.4", snmp_profile, site=site, status="inactive")
        cfg = build_config(collector)
        assert cfg["devices"] == []

    def test_includes_service_checks(self, collector, snmp_profile):
        site = Site.objects.create(name="DC-C", default_collector=collector)
        d = _device("dev", "10.0.0.5", snmp_profile, site=site)
        ServiceCheck.objects.create(name="ping", check_type="icmp", host="10.0.0.5", device=d, is_active=True)
        cfg = build_config(collector)
        assert len(cfg["checks"]) == 1
        assert cfg["checks"][0]["name"] == "ping" and cfg["checks"][0]["check_type"] == "icmp"

    def test_revision_is_stable_and_change_sensitive(self, collector, snmp_profile):
        site = Site.objects.create(name="DC-D", default_collector=collector)
        _device("a", "10.0.0.6", snmp_profile, site=site)
        rev1 = build_config(collector)["revision"]
        # Same inputs → same revision (timestamp excluded from the hash).
        assert build_config(collector)["revision"] == rev1
        # New device → revision changes.
        _device("b", "10.0.0.7", snmp_profile, site=site)
        assert build_config(collector)["revision"] != rev1

    def test_empty_for_unassigned_collector(self, collector, snmp_profile):
        _device("loose", "10.0.0.8", snmp_profile)
        cfg = build_config(collector)
        assert cfg["devices"] == [] and cfg["checks"] == []


class TestDevicesForResolver:
    """resolve.devices_for_collector must be the exact inverse of effective_collector."""

    def test_resolver_matches_effective_collector(self, snmp_profile):
        from apps.collectors.resolve import devices_for_collector, effective_collector

        a = Collector.objects.create(name="A", collector_type="remote", api_key_hash="ra")
        glob = Collector.objects.create(name="G", collector_type="local", api_key_hash="rg", is_default=True)
        site_a = Site.objects.create(name="SA", default_collector=a)
        _device("pinned", "10.1.0.1", snmp_profile, collector=a)        # device.collector tier
        via_site = _device("via-site", "10.1.0.2", snmp_profile, site=site_a)  # site.default tier
        loose = _device("loose", "10.1.0.3", snmp_profile)             # global is_default tier

        assert set(devices_for_collector(a).values_list("hostname", flat=True)) == {"pinned", "via-site"}
        assert set(devices_for_collector(glob).values_list("hostname", flat=True)) == {"loose"}
        assert effective_collector(via_site).id == a.id
        assert effective_collector(loose).id == glob.id


class TestRepublishOwnership:
    """Ownership-moving changes refresh OLD ∪ NEW owners (no stale double-poll)."""

    def _capture(self, monkeypatch):
        from apps.collectors import signals
        seen: list[set] = []
        monkeypatch.setattr(signals, "_republish_ids", lambda ids: seen.append({i for i in ids if i}))
        return seen

    def test_device_reassignment_refreshes_old_and_new(self, settings, monkeypatch):
        settings.COLLECTOR_CONFIG_PUBLISH = True
        seen = self._capture(monkeypatch)
        a = Collector.objects.create(name="A", collector_type="remote", api_key_hash="ra")
        b = Collector.objects.create(name="B", collector_type="remote", api_key_hash="rb")
        d = Device.objects.create(hostname="d", ip_address="10.2.0.1", platform="ios_xe", collector=a)
        seen.clear()
        d.collector = b
        d.save()
        assert seen[-1] == {a.id, b.id}

    def test_site_default_move_refreshes_old_and_new(self, settings, monkeypatch):
        settings.COLLECTOR_CONFIG_PUBLISH = True
        seen = self._capture(monkeypatch)
        a = Collector.objects.create(name="A", collector_type="remote", api_key_hash="ra")
        b = Collector.objects.create(name="B", collector_type="remote", api_key_hash="rb")
        site = Site.objects.create(name="S", default_collector=a)
        seen.clear()
        site.default_collector = b
        site.save()
        assert seen[-1] == {a.id, b.id}

    def test_config_change_refreshes_current_owner_only(self, settings, monkeypatch):
        settings.COLLECTOR_CONFIG_PUBLISH = True
        seen = self._capture(monkeypatch)
        a = Collector.objects.create(name="A", collector_type="remote", api_key_hash="ra")
        d = Device.objects.create(hostname="d", ip_address="10.2.0.2", platform="ios_xe", collector=a)
        seen.clear()
        d.hostname = "d-renamed"  # config change, not an ownership move
        d.save()
        assert seen[-1] == {a.id}
