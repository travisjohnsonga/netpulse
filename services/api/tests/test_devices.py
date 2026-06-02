import pytest
from apps.devices.models import Device, DeviceGroup, DiscoveredDevice, DiscoveryJob, Site

pytestmark = pytest.mark.django_db


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def site():
    return Site.objects.create(name="DC-1", location="New York")


@pytest.fixture
def device(site):
    return Device.objects.create(
        hostname="core-rtr-01",
        ip_address="10.0.0.1",
        vendor="Cisco",
        platform=Device.Platform.IOS_XE,
        site=site,
    )


@pytest.fixture
def group():
    return DeviceGroup.objects.create(name="Routers")


# ── Site CRUD ─────────────────────────────────────────────────────────────────

class TestSiteEndpoints:
    def test_list_sites(self, auth_client, site):
        resp = auth_client.get("/api/devices/sites/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_site(self, auth_client):
        resp = auth_client.post("/api/devices/sites/", {"name": "Branch-1", "location": "Austin"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "Branch-1"

    def test_create_site_duplicate_name(self, auth_client, site):
        resp = auth_client.post("/api/devices/sites/", {"name": "DC-1"})
        assert resp.status_code == 400

    def test_retrieve_site(self, auth_client, site):
        resp = auth_client.get(f"/api/devices/sites/{site.pk}/")
        assert resp.status_code == 200
        assert resp.json()["name"] == "DC-1"

    def test_update_site(self, auth_client, site):
        resp = auth_client.patch(f"/api/devices/sites/{site.pk}/", {"location": "New Jersey"})
        assert resp.status_code == 200
        assert resp.json()["location"] == "New Jersey"

    def test_delete_site(self, auth_client, site):
        resp = auth_client.delete(f"/api/devices/sites/{site.pk}/")
        assert resp.status_code == 204
        assert not Site.objects.filter(pk=site.pk).exists()

    def test_timestamps_present(self, auth_client, site):
        resp = auth_client.get(f"/api/devices/sites/{site.pk}/")
        data = resp.json()
        assert "created_at" in data
        assert "updated_at" in data

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/devices/sites/")
        assert resp.status_code == 401


# ── DeviceGroup CRUD ──────────────────────────────────────────────────────────

class TestDeviceGroupEndpoints:
    def test_list_groups(self, auth_client, group):
        resp = auth_client.get("/api/devices/groups/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_group(self, auth_client):
        resp = auth_client.post("/api/devices/groups/", {"name": "Switches"})
        assert resp.status_code == 201
        assert resp.json()["name"] == "Switches"

    def test_retrieve_group(self, auth_client, group):
        resp = auth_client.get(f"/api/devices/groups/{group.pk}/")
        assert resp.status_code == 200

    def test_delete_group(self, auth_client, group):
        resp = auth_client.delete(f"/api/devices/groups/{group.pk}/")
        assert resp.status_code == 204


# ── Device CRUD ───────────────────────────────────────────────────────────────

class TestDeviceEndpoints:
    def test_list_devices(self, auth_client, device):
        resp = auth_client.get("/api/devices/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_list_uses_lightweight_serializer(self, auth_client, device):
        resp = auth_client.get("/api/devices/")
        item = resp.json()["results"][0]
        # Lightweight list serializer — carries the fields the configurable
        # Devices columns need, but not the full device record (no groups, etc.).
        assert set(item.keys()) == {
            "id", "hostname", "ip_address", "management_ip", "platform", "vendor",
            "model", "os_version", "serial_number", "status", "site_name",
            "credential_profile", "last_seen", "is_reachable", "consecutive_failures",
            "last_reachability_check", "unreachable_since", "notes", "created_at",
        }

    def test_list_includes_site_name(self, auth_client, device, site):
        resp = auth_client.get("/api/devices/")
        assert resp.json()["results"][0]["site_name"] == "DC-1"

    def test_list_site_name_null_when_no_site(self, auth_client):
        Device.objects.create(hostname="rtr-02", ip_address="10.0.0.2")
        resp = auth_client.get("/api/devices/")
        assert resp.json()["results"][0]["site_name"] is None

    def test_create_device(self, auth_client):
        resp = auth_client.post("/api/devices/", {
            "hostname": "edge-fw-01",
            "ip_address": "192.168.1.1",
            "vendor": "Palo Alto",
            "platform": "other",
        })
        assert resp.status_code == 201
        assert resp.json()["hostname"] == "edge-fw-01"

    def test_create_device_default_status_active(self, auth_client):
        resp = auth_client.post("/api/devices/", {
            "hostname": "sw-01",
            "ip_address": "10.0.1.1",
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"

    def test_create_device_same_hostname_upserts(self, auth_client, device):
        # Re-adding an existing hostname updates that row in place (stable PK),
        # returning 200 — not a duplicate or a 400.
        resp = auth_client.post("/api/devices/", {
            "hostname": "core-rtr-01",
            "ip_address": "10.99.99.99",
        })
        assert resp.status_code == 200
        assert resp.json()["id"] == device.pk
        device.refresh_from_db()
        assert device.ip_address == "10.99.99.99"
        assert Device.objects.filter(hostname="core-rtr-01").count() == 1

    def test_create_device_duplicate_ip(self, auth_client, device):
        resp = auth_client.post("/api/devices/", {
            "hostname": "another-rtr",
            "ip_address": "10.0.0.1",
        })
        assert resp.status_code == 400

    def test_retrieve_device_full_serializer(self, auth_client, device):
        resp = auth_client.get(f"/api/devices/{device.pk}/")
        data = resp.json()
        assert "vendor" in data
        assert "os_version" in data
        assert "serial_number" in data
        assert "notes" in data

    def test_update_device_status(self, auth_client, device):
        resp = auth_client.patch(f"/api/devices/{device.pk}/", {"status": "maintenance"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "maintenance"

    def test_delete_device(self, auth_client, device):
        resp = auth_client.delete(f"/api/devices/{device.pk}/")
        assert resp.status_code == 204
        assert not Device.objects.filter(pk=device.pk).exists()

    def test_filter_by_status(self, auth_client, device, site):
        Device.objects.create(hostname="rtr-maint", ip_address="10.0.0.3", status="maintenance")
        resp = auth_client.get("/api/devices/?status=active")
        assert resp.status_code == 200
        assert all(d["status"] == "active" for d in resp.json()["results"])

    def test_filter_by_platform(self, auth_client, device):
        Device.objects.create(hostname="juniper-01", ip_address="10.0.0.4", platform="junos")
        resp = auth_client.get("/api/devices/?platform=ios_xe")
        assert resp.status_code == 200
        assert all(d["platform"] == "ios_xe" for d in resp.json()["results"])

    def test_search_by_hostname(self, auth_client, device):
        Device.objects.create(hostname="branch-sw-01", ip_address="10.0.0.5")
        resp = auth_client.get("/api/devices/?search=core")
        assert resp.status_code == 200
        hostnames = [d["hostname"] for d in resp.json()["results"]]
        assert "core-rtr-01" in hostnames
        assert "branch-sw-01" not in hostnames

    def test_ordering_by_hostname(self, auth_client, device):
        Device.objects.create(hostname="aaa-device", ip_address="10.0.0.6")
        resp = auth_client.get("/api/devices/?ordering=hostname")
        assert resp.status_code == 200
        hostnames = [d["hostname"] for d in resp.json()["results"]]
        assert hostnames == sorted(hostnames)

    def test_device_with_group(self, auth_client, device, group):
        device.groups.add(group)
        resp = auth_client.get(f"/api/devices/{device.pk}/")
        assert group.pk in resp.json()["groups"]

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/devices/")
        assert resp.status_code == 401


# ── Device Model ──────────────────────────────────────────────────────────────

class TestDeviceModel:
    def test_str(self, device):
        assert str(device) == "core-rtr-01"

    def test_status_choices(self):
        for value, _ in Device.Status.choices:
            assert value in ("active", "inactive", "unreachable", "maintenance", "decommissioned")

    def test_platform_choices(self):
        for value, _ in Device.Platform.choices:
            assert value in ("ios", "ios_xe", "ios_xr", "nxos", "eos", "junos",
                             "fortios", "panos", "sonicwall", "aos_cx", "aruba",
                             "sonic", "other")

    def test_site_nullable(self):
        d = Device.objects.create(hostname="no-site", ip_address="172.16.0.1")
        assert d.site is None

    def test_management_ip_optional(self, device):
        assert device.management_ip is None


class TestDeviceLldpNeighbors:
    """The metrics endpoint surfaces LLDP neighbours from TopologyLink rows in
    EITHER direction, independent of per-interface lldp_* metadata."""

    def test_neighbors_both_directions(self, auth_client, device, site):
        from apps.devices.models import TopologyLink
        peer_a = Device.objects.create(hostname="peer-a", ip_address="10.0.0.2", site=site)
        peer_b = Device.objects.create(hostname="peer-b", ip_address="10.0.0.3", site=site)
        # device is device_a on one link, device_b on the other.
        TopologyLink.objects.create(device_a=device, port_a="Gi1", device_b=peer_a, port_b="Gi0/1")
        TopologyLink.objects.create(device_a=peer_b, port_a="Gi2", device_b=device, port_b="Gi3")

        resp = auth_client.get(f"/api/devices/{device.id}/metrics/")
        assert resp.status_code == 200
        neighbors = {n["neighbor_hostname"]: n for n in resp.json()["lldp_neighbors"]}
        assert set(neighbors) == {"peer-a", "peer-b"}
        # Local/remote ports are oriented from this device's perspective.
        assert neighbors["peer-a"]["local_port"] == "Gi1" and neighbors["peer-a"]["remote_port"] == "Gi0/1"
        assert neighbors["peer-b"]["local_port"] == "Gi3" and neighbors["peer-b"]["remote_port"] == "Gi2"


# ── DiscoveryJob ──────────────────────────────────────────────────────────────

class TestDiscoveryJobModel:
    def test_create_scan_job(self, user):
        job = DiscoveryJob.objects.create(
            name="DC-1 Scan",
            method=DiscoveryJob.Method.SCAN,
            subnets=["10.0.0.0/24"],
            allowed_subnets=["10.0.0.0/24"],
            created_by=user,
        )
        assert job.status == DiscoveryJob.Status.PENDING
        assert job.devices_found == 0

    def test_default_safety_limits(self, user):
        job = DiscoveryJob.objects.create(
            name="Safe Job",
            method=DiscoveryJob.Method.SCAN,
            created_by=user,
        )
        assert job.max_depth == 10
        assert job.max_devices == 1000
        assert job.rate_limit_pps == 10

    def test_str(self, user):
        job = DiscoveryJob.objects.create(name="Test", method="scan", created_by=user)
        assert "Test" in str(job)
        assert "scan" in str(job)

    def test_topology_method_with_seed(self, user, device):
        job = DiscoveryJob.objects.create(
            name="Topo Walk",
            method=DiscoveryJob.Method.TOPOLOGY,
            seed_device=device,
            created_by=user,
        )
        assert job.seed_device == device


# ── DiscoveredDevice ──────────────────────────────────────────────────────────

class TestDiscoveredDeviceModel:
    def test_create_discovered_device(self, user):
        job = DiscoveryJob.objects.create(name="Job", method="scan", created_by=user)
        dd = DiscoveredDevice.objects.create(
            job=job,
            source_ip="10.0.0.50",
            confidence_score=60,
            discovered_hostname="sw-floor1",
            detection_methods=["snmp"],
            responds_to={"snmp": True},
        )
        assert dd.status == DiscoveredDevice.Status.PENDING
        assert dd.confidence_score == 60

    def test_unique_per_job_and_ip(self, user):
        from django.db import IntegrityError
        job = DiscoveryJob.objects.create(name="Job", method="scan", created_by=user)
        DiscoveredDevice.objects.create(job=job, source_ip="10.0.0.51")
        with pytest.raises(IntegrityError):
            DiscoveredDevice.objects.create(job=job, source_ip="10.0.0.51")

    def test_same_ip_different_jobs_allowed(self, user):
        job1 = DiscoveryJob.objects.create(name="J1", method="scan", created_by=user)
        job2 = DiscoveryJob.objects.create(name="J2", method="scan", created_by=user)
        DiscoveredDevice.objects.create(job=job1, source_ip="10.0.0.52")
        dd2 = DiscoveredDevice.objects.create(job=job2, source_ip="10.0.0.52")
        assert dd2.pk is not None

    def test_approval_sets_approved_device(self, user, device):
        job = DiscoveryJob.objects.create(name="Job", method="scan", created_by=user)
        dd = DiscoveredDevice.objects.create(job=job, source_ip="10.0.0.53")
        from django.utils import timezone
        dd.status = DiscoveredDevice.Status.APPROVED
        dd.approved_device = device
        dd.approved_by = user
        dd.approved_at = timezone.now()
        dd.save()
        dd.refresh_from_db()
        assert dd.approved_device == device


class TestConnectionEndpoint:
    def test_requires_ip(self, auth_client):
        resp = auth_client.post("/api/devices/test-connection/", {}, format="json")
        assert resp.status_code == 400

    def test_probe_returns_fingerprint_shape(self, auth_client):
        # 127.0.0.1 management ports are closed in the test env → fast, unreachable.
        resp = auth_client.post(
            "/api/devices/test-connection/", {"ip": "127.0.0.1"}, format="json"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == {
            "reachable", "open_ports", "banner",
            "vendor", "platform", "os_version", "model", "detail",
        }
        assert isinstance(body["open_ports"], list)

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.post(
            "/api/devices/test-connection/", {"ip": "10.0.0.1"}, format="json"
        )
        assert resp.status_code == 401


class TestSitesTopLevelEndpoint:
    def test_list_and_create_at_api_sites(self, auth_client):
        resp = auth_client.post("/api/sites/", {"name": "DC-West", "site_type": "datacenter", "city": "Reno"}, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["slug"] == "dc-west"
        assert body["device_count"] == 0
        assert auth_client.get("/api/sites/").json()["count"] >= 1

    def test_site_devices_action(self, auth_client, site, device):
        resp = auth_client.get(f"/api/sites/{site.pk}/devices/")
        assert resp.status_code == 200
        assert any(d["hostname"] == device.hostname for d in resp.json())

    def test_parent_hierarchy(self, auth_client):
        parent = auth_client.post("/api/sites/", {"name": "Region-1"}, format="json").json()
        child = auth_client.post("/api/sites/", {"name": "Branch-1", "parent_site": parent["id"]}, format="json")
        assert child.status_code == 201
        assert child.json()["parent_site_name"] == "Region-1"


class TestDetectPlatformEndpoint:
    @pytest.fixture
    def ssh_profile(self):
        from apps.credentials.models import CredentialProfile
        return CredentialProfile.objects.create(
            name="detect-ssh", ssh_enabled=True, ssh_username="netmagic", vault_path="x")

    def test_requires_fields(self, auth_client):
        resp = auth_client.post("/api/devices/detect-platform/", {"ip": "10.0.0.1"}, format="json")
        assert resp.status_code == 400  # missing credential_profile_id

    def test_missing_profile(self, auth_client):
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "10.0.0.1", "credential_profile_id": 9999}, format="json")
        assert resp.status_code == 400
        assert resp.json()["detected"] is False

    def test_ssh_not_enabled(self, auth_client):
        from apps.credentials.models import CredentialProfile
        p = CredentialProfile.objects.create(name="snmp-only", snmpv2c_enabled=True, vault_path="x")
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "10.0.0.1", "credential_profile_id": p.id}, format="json")
        assert resp.status_code == 400
        assert resp.json()["error"] == "ssh_not_enabled"

    def test_successful_detection(self, auth_client, ssh_profile, monkeypatch):
        from apps.devices import detect
        monkeypatch.setattr(detect, "_ssh_detect", lambda *a, **k: ("cisco_xe", {"cisco_xe": 99, "cisco_ios": 5}))
        monkeypatch.setattr(detect, "_collect_version", lambda *a, **k: {
            "os_version": "17.12.1", "model": "C8000V", "serial": "9W7Q57VMXHY", "hostname": "router1"})
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "192.168.98.100", "credential_profile_id": ssh_profile.id}, format="json")
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["detected"] is True
        assert body["device_type"] == "cisco_xe"
        assert body["vendor"] == "cisco" and body["platform"] == "ios_xe"
        assert body["os_version"] == "17.12.1" and body["model"] == "C8000V"
        assert body["hostname"] == "router1" and body["confidence"] == "high"

    def test_auth_failure(self, auth_client, ssh_profile, monkeypatch):
        from apps.devices import detect
        class NetmikoAuthenticationException(Exception):
            pass
        def boom(*a, **k):
            raise NetmikoAuthenticationException("bad creds")
        monkeypatch.setattr(detect, "_ssh_detect", boom)
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "10.0.0.1", "credential_profile_id": ssh_profile.id}, format="json")
        assert resp.status_code == 200
        assert resp.json() == {"detected": False, "error": "auth_failed"} or resp.json()["error"] == "auth_failed"

    def test_unknown_platform(self, auth_client, ssh_profile, monkeypatch):
        from apps.devices import detect, fingerprint
        monkeypatch.setattr(detect, "_ssh_detect", lambda *a, **k: (None, {}))
        monkeypatch.setattr(fingerprint, "_ssh_banner", lambda *a, **k: "")  # no banner hint
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "10.0.0.1", "credential_profile_id": ssh_profile.id}, format="json")
        assert resp.json()["detected"] is False
        assert resp.json()["error"] == "unknown"

    def test_fortios_banner_fallback(self, auth_client, ssh_profile, monkeypatch):
        # SSHDetect can't fingerprint FortiOS → fall back to the SSH banner.
        from apps.devices import detect, fingerprint
        monkeypatch.setattr(detect, "_ssh_detect", lambda *a, **k: (None, {}))
        monkeypatch.setattr(fingerprint, "_ssh_banner", lambda *a, **k: "SSH-2.0-FortiGate-100F")
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "10.0.0.1", "credential_profile_id": ssh_profile.id}, format="json")
        body = resp.json()
        assert body["detected"] is True
        assert body["vendor"] == "fortinet" and body["platform"] == "fortios"
        assert body["confidence"] == "low"


class TestDetectParsing:
    def test_parse_version_cisco(self):
        from apps.devices.detect import parse_version
        out = "Cisco IOS XE Software, Version 17.12.1\ncisco C8000V (VXE) processor\nProcessor board ID 9W7Q57VMXHY"
        info = parse_version(out, "router1#")
        assert info["os_version"] == "17.12.1"
        assert info["hostname"] == "router1"
        assert info["serial"] == "9W7Q57VMXHY"

    def test_netmiko_mapping_complete(self):
        from apps.devices.detect import NETMIKO_TO_NETPULSE
        assert NETMIKO_TO_NETPULSE["cisco_xe"] == {"vendor": "cisco", "platform": "ios_xe"}
        assert NETMIKO_TO_NETPULSE["juniper_junos"]["platform"] == "junos"
