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


# ── Device list summary endpoints ─────────────────────────────────────────────

class TestDeviceStatusSummary:
    """status-summary feeds the count-based Devices cards (Total/Up/Down). Counts
    the network-device set from the DB (the list is paginated). up = reachable &
    not unreachable; down = the complement — matching the Up/Down badge."""

    def test_counts_up_down_over_network_devices(self, auth_client, site):
        Device.objects.create(hostname="up1", ip_address="10.0.0.1", status="active", is_reachable=True)
        Device.objects.create(hostname="up2", ip_address="10.0.0.2", status="active", is_reachable=True)
        Device.objects.create(hostname="down1", ip_address="10.0.0.3", status="unreachable", is_reachable=False)
        # An agent-backed server must NOT be counted (not a network device).
        Device.objects.create(hostname="srv", ip_address="127.0.0.1", status="active",
                              device_kind=Device.DeviceKind.SERVER)
        body = auth_client.get("/api/devices/status-summary/").json()
        assert body == {"total": 3, "up": 2, "down": 1}

    def test_site_scoped(self, auth_client, site):
        other = Site.objects.create(name="DC-2")
        Device.objects.create(hostname="a", ip_address="10.1.0.1", status="active", is_reachable=True, site=site)
        Device.objects.create(hostname="b", ip_address="10.1.0.2", status="unreachable", is_reachable=False, site=other)
        body = auth_client.get(f"/api/devices/status-summary/?site={site.pk}").json()
        assert body == {"total": 1, "up": 1, "down": 0}

    def test_metrics_summary_endpoint_ok(self, auth_client, device):
        # InfluxDB isn't available in tests → returns an empty list, not a 500.
        r = auth_client.get("/api/devices/metrics-summary/")
        assert r.status_code == 200 and isinstance(r.json(), list)


# ── Device CRUD ───────────────────────────────────────────────────────────────

class TestServerDevicesExcludedFromList:
    """device_kind is the single source of truth (replacing the agent__isnull/
    synthetic-IP heuristic): SERVER devices (agent-backed) live on the Servers
    page and are excluded from the Devices LIST, but still resolve via retrieve.
    Set authoritatively at creation, never re-inferred at query time."""

    def test_server_kind_excluded_network_kind_included(self, auth_client, device):
        # core-rtr-01 (default network_device) stays; a server is hidden — even
        # with a perfectly real IP (no IP heuristic involved anymore).
        Device.objects.create(hostname="srv-01", ip_address="172.18.0.24",
                              device_kind=Device.DeviceKind.SERVER)
        resp = auth_client.get("/api/devices/")
        assert resp.status_code == 200
        hostnames = {r["hostname"] for r in resp.json()["results"]}
        assert "core-rtr-01" in hostnames
        assert "srv-01" not in hostnames
        assert resp.json()["count"] == 1

    def test_server_device_still_retrievable(self, auth_client):
        dev = Device.objects.create(hostname="srv-02", ip_address="10.5.5.5",
                                    device_kind=Device.DeviceKind.SERVER)
        assert auth_client.get(f"/api/devices/{dev.id}/").status_code == 200

    def test_list_endpoint_200_real_and_no_servers(self, auth_client, device):
        # Guards the #136 regression: the actual endpoint must return 200 with the
        # right contents (not just the filter logic in isolation).
        Device.objects.create(hostname="real-sw", ip_address="192.168.1.2")
        Device.objects.create(hostname="agent-host", ip_address="10.9.9.9",
                              device_kind=Device.DeviceKind.SERVER)
        resp = auth_client.get("/api/devices/")
        assert resp.status_code == 200
        hostnames = {r["hostname"] for r in resp.json()["results"]}
        assert {"core-rtr-01", "real-sw"} <= hostnames and "agent-host" not in hostnames

    def test_ensure_agent_device_tags_server_at_creation(self):
        # The #118 self-heal authoritatively classifies the created Device.
        from apps.agents.models import Agent
        from apps.agents.device_link import ensure_agent_device
        a = Agent.objects.create(hostname="newsrv", status=Agent.Status.ACTIVE)
        dev = ensure_agent_device(a)
        assert dev.device_kind == Device.DeviceKind.SERVER

    def test_manual_created_device_defaults_network_device(self, auth_client):
        resp = auth_client.post("/api/devices/", {"hostname": "sw-99", "ip_address": "10.7.7.7"})
        assert resp.status_code == 201
        assert Device.objects.get(hostname="sw-99").device_kind == Device.DeviceKind.NETWORK_DEVICE


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
            "id", "hostname", "display_hostname", "ip_address", "management_ip",
            "ip_locked",
            "platform", "vendor", "model", "os_version", "serial_number", "status",
            "site_name", "role", "credential_profile", "last_seen", "is_reachable",
            "consecutive_failures", "last_reachability_check", "unreachable_since",
            "compliance_score", "compliance_grade",
            "notes", "created_at",
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

    def test_assign_device_to_site(self, auth_client, device):
        # Site detail "Add to site" PATCHes the device's site FK.
        other = Site.objects.create(name="Branch-9")
        resp = auth_client.patch(f"/api/devices/{device.pk}/", {"site": other.pk})
        assert resp.status_code == 200
        device.refresh_from_db()
        assert device.site_id == other.pk

    def test_unassign_device_from_site(self, auth_client, device):
        # "Remove" clears the site FK (device kept, not deleted). JSON-encoded
        # because the multipart test client can't represent a null value.
        resp = auth_client.patch(
            f"/api/devices/{device.pk}/", {"site": None}, format="json"
        )
        assert resp.status_code == 200
        device.refresh_from_db()
        assert device.site_id is None

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
                             "unifi_ap", "unifi_sw", "unifi_gw", "unifi_udm",
                             "unifi_uckp", "unifi_ucg", "mist_ap", "mist_sw",
                             "mist_gw", "sonic", "other")

    def test_platforms_endpoint(self, auth_client):
        resp = auth_client.get("/api/devices/platforms/")
        assert resp.status_code == 200
        body = resp.json()
        values = {p["value"] for p in body}
        assert {"ios_xe", "fortios", "sonicwall", "aos_cx", "aruba"} <= values
        assert all("label" in p for p in body)

    def test_platforms_endpoint_requires_auth(self, api_client):
        assert api_client.get("/api/devices/platforms/").status_code == 401

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

    def test_device_status_breakdown(self, auth_client, site):
        # up: reachable + active; down: unreachable status or is_reachable=False;
        # maintenance falls in neither bucket but still counts toward the total.
        Device.objects.create(hostname="up-1", ip_address="10.0.1.1", platform=Device.Platform.IOS_XE,
                              site=site, is_reachable=True, status=Device.Status.ACTIVE)
        Device.objects.create(hostname="up-2", ip_address="10.0.1.2", platform=Device.Platform.IOS_XE,
                              site=site, is_reachable=True, status=Device.Status.ACTIVE)
        Device.objects.create(hostname="down-1", ip_address="10.0.1.3", platform=Device.Platform.IOS_XE,
                              site=site, is_reachable=False, status=Device.Status.UNREACHABLE)
        Device.objects.create(hostname="maint-1", ip_address="10.0.1.4", platform=Device.Platform.IOS_XE,
                              site=site, is_reachable=True, status=Device.Status.MAINTENANCE)

        # In the list response
        row = next(s for s in auth_client.get("/api/sites/").json()["results"] if s["id"] == site.pk)
        assert row["device_count"] == 4
        assert row["devices_up"] == 2
        assert row["devices_down"] == 1
        assert row["devices_unknown"] == 0

        # And in the detail response
        body = auth_client.get(f"/api/sites/{site.pk}/").json()
        assert (body["device_count"], body["devices_up"], body["devices_down"]) == (4, 2, 1)

    def test_status_counts_zero_on_create(self, auth_client):
        # The create response isn't annotated — the serializer fallback must still
        # return zeros rather than erroring.
        body = auth_client.post("/api/sites/", {"name": "Empty-DC"}, format="json").json()
        assert body["devices_up"] == 0
        assert body["devices_down"] == 0
        assert body["devices_unknown"] == 0


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

    def test_aos_cx_detection(self, auth_client, ssh_profile, monkeypatch):
        # Netmiko fingerprints AOS-CX as "aruba_aoscx" → maps to vendor aruba / platform aos_cx.
        from apps.devices import detect
        monkeypatch.setattr(detect, "_ssh_detect", lambda *a, **k: ("aruba_aoscx", {"aruba_aoscx": 99}))
        monkeypatch.setattr(detect, "_collect_version", lambda *a, **k: {
            "os_version": "FL.10.10.1010", "model": "6300M", "serial": "SG12345", "hostname": "core-sw-1"})
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "10.0.0.2", "credential_profile_id": ssh_profile.id}, format="json")
        body = resp.json()
        assert body["detected"] is True
        assert body["device_type"] == "aruba_aoscx"
        assert body["vendor"] == "aruba" and body["platform"] == "aos_cx"

    def test_aos_cx_banner_fallback(self, auth_client, ssh_profile, monkeypatch):
        # SSHDetect can't fingerprint → fall back to the SSH banner carrying "ArubaOS-CX".
        from apps.devices import detect, fingerprint
        monkeypatch.setattr(detect, "_ssh_detect", lambda *a, **k: (None, {}))
        monkeypatch.setattr(fingerprint, "_ssh_banner", lambda *a, **k: "SSH-2.0-ArubaOS-CX")
        resp = auth_client.post("/api/devices/detect-platform/",
                                {"ip": "10.0.0.2", "credential_profile_id": ssh_profile.id}, format="json")
        body = resp.json()
        assert body["detected"] is True
        assert body["vendor"] == "aruba" and body["platform"] == "aos_cx"

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


# ── Device Roles ──────────────────────────────────────────────────────────────

@pytest.fixture
def role():
    from apps.devices.models import DeviceRole
    return DeviceRole.objects.create(name="Firewall", color="#ef4444")


class TestDeviceRoleEndpoints:
    def test_role_slug_autogenerated(self, role):
        assert role.slug == "firewall"

    def test_list_roles(self, auth_client, role):
        resp = auth_client.get("/api/devices/roles/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_role(self, auth_client):
        resp = auth_client.post(
            "/api/devices/roles/",
            {"name": "Core Switch", "color": "#3b82f6"}, format="json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["slug"] == "core-switch"
        assert body["color"] == "#3b82f6"
        assert body["device_count"] == 0

    def test_assign_role_to_device_via_role_id(self, auth_client, device, role):
        resp = auth_client.patch(
            f"/api/devices/{device.id}/", {"role_id": role.id}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        # Nested role object on read.
        assert body["role"]["id"] == role.id
        assert body["role"]["name"] == "Firewall"
        assert body["role"]["color"] == "#ef4444"

    def test_device_list_includes_role(self, auth_client, device, role):
        device.role = role
        device.save(update_fields=["role"])
        resp = auth_client.get("/api/devices/")
        assert resp.status_code == 200
        row = next(r for r in resp.json()["results"] if r["id"] == device.id)
        assert row["role"]["slug"] == "firewall"

    def test_filter_devices_by_role(self, auth_client, device, role):
        device.role = role
        device.save(update_fields=["role"])
        resp = auth_client.get(f"/api/devices/?role={role.id}")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_cannot_delete_role_in_use(self, auth_client, device, role):
        device.role = role
        device.save(update_fields=["role"])
        resp = auth_client.delete(f"/api/devices/roles/{role.id}/")
        assert resp.status_code == 409

    def test_delete_unused_role(self, auth_client, role):
        resp = auth_client.delete(f"/api/devices/roles/{role.id}/")
        assert resp.status_code == 204

    def test_seed_device_roles_idempotent(self):
        from django.core.management import call_command
        from apps.devices.models import DeviceRole
        call_command("seed_device_roles")
        first = DeviceRole.objects.count()
        assert first == 8
        call_command("seed_device_roles")
        assert DeviceRole.objects.count() == first


# ── Hostname Rules ──────────────────────────────────────────────────────────────

@pytest.fixture
def core_role():
    from apps.devices.models import DeviceRole
    return DeviceRole.objects.create(name="Core Switch", color="#3b82f6")


@pytest.fixture
def fw_role():
    from apps.devices.models import DeviceRole
    return DeviceRole.objects.create(name="Firewall", color="#ef4444")


@pytest.fixture
def wco2_site():
    return Site.objects.create(name="WCO2", location="West")


def _make_rule(**kwargs):
    from apps.devices.models import HostnameRule
    return HostnameRule.objects.create(**kwargs)


class TestHostnameRuleModel:
    def test_matches_case_insensitive(self):
        from apps.devices.models import HostnameRule
        rule = HostnameRule(name="x", pattern=r"-(crt|mdf)-")
        assert rule.matches("WCO2-MDF-CRT-01")
        assert not rule.matches("router1.local")

    def test_invalid_regex_never_matches(self):
        from apps.devices.models import HostnameRule
        rule = HostnameRule(name="x", pattern=r"[unclosed")
        assert rule.matches("anything") is False


class TestApplyHostnameRules:
    def test_assigns_role_and_site(self, core_role, wco2_site):
        from apps.devices.hostname_rules import apply_hostname_rules
        from apps.devices.models import HostnameRule
        _make_rule(name="site", pattern=r"^wco2-", rule_type=HostnameRule.RuleType.SITE,
                   site=wco2_site, priority=10)
        _make_rule(name="core", pattern=r"-(crt|mdf)-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20)
        dev = Device.objects.create(hostname="wco2-mdf-crt-01", ip_address="10.9.0.1")
        role_assigned, site_assigned = apply_hostname_rules(dev)
        assert role_assigned and site_assigned
        dev.refresh_from_db()
        assert dev.role_id == core_role.id
        assert dev.site_id == wco2_site.id

    def test_does_not_override_existing(self, core_role, fw_role, site):
        from apps.devices.hostname_rules import apply_hostname_rules
        from apps.devices.models import HostnameRule
        _make_rule(name="core", pattern=r"-crt-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20)
        dev = Device.objects.create(
            hostname="x-crt-1", ip_address="10.9.0.2", role=fw_role, site=site)
        role_assigned, _ = apply_hostname_rules(dev)
        assert role_assigned is False
        dev.refresh_from_db()
        assert dev.role_id == fw_role.id

    def test_force_overrides_existing(self, core_role, fw_role):
        from apps.devices.hostname_rules import apply_hostname_rules
        from apps.devices.models import HostnameRule
        _make_rule(name="core", pattern=r"-crt-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20)
        dev = Device.objects.create(hostname="x-crt-1", ip_address="10.9.0.3", role=fw_role)
        role_assigned, _ = apply_hostname_rules(dev, force=True)
        assert role_assigned
        dev.refresh_from_db()
        assert dev.role_id == core_role.id

    def test_first_match_wins_by_priority(self, core_role, fw_role):
        from apps.devices.hostname_rules import apply_hostname_rules
        from apps.devices.models import HostnameRule
        # Both match; lower priority number wins.
        _make_rule(name="b", pattern=r"-x-", rule_type=HostnameRule.RuleType.ROLE,
                   role=fw_role, priority=50)
        _make_rule(name="a", pattern=r"-x-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=10)
        dev = Device.objects.create(hostname="dev-x-1", ip_address="10.9.0.4")
        apply_hostname_rules(dev)
        dev.refresh_from_db()
        assert dev.role_id == core_role.id

    def test_disabled_rules_ignored(self, core_role):
        from apps.devices.hostname_rules import apply_hostname_rules
        from apps.devices.models import HostnameRule
        _make_rule(name="core", pattern=r"-crt-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20, enabled=False)
        dev = Device.objects.create(hostname="x-crt-1", ip_address="10.9.0.5")
        role_assigned, _ = apply_hostname_rules(dev)
        assert role_assigned is False


class TestHostnameRuleEndpoints:
    def test_crud(self, auth_client, core_role):
        from apps.devices.models import HostnameRule
        resp = auth_client.post("/api/devices/hostname-rules/", {
            "name": "Core", "pattern": r"-crt-", "rule_type": "role",
            "role": core_role.id, "priority": 20,
        }, format="json")
        assert resp.status_code == 201, resp.json()
        rule_id = resp.json()["id"]
        assert resp.json()["role_name"] == "Core Switch"

        resp = auth_client.get("/api/devices/hostname-rules/")
        assert resp.json()["count"] == 1

        resp = auth_client.patch(
            f"/api/devices/hostname-rules/{rule_id}/", {"priority": 5}, format="json")
        assert resp.status_code == 200
        assert HostnameRule.objects.get(id=rule_id).priority == 5

        resp = auth_client.delete(f"/api/devices/hostname-rules/{rule_id}/")
        assert resp.status_code == 204

    def test_invalid_regex_rejected(self, auth_client):
        resp = auth_client.post("/api/devices/hostname-rules/", {
            "name": "Bad", "pattern": r"[unclosed", "rule_type": "role",
        }, format="json")
        assert resp.status_code == 400
        assert "pattern" in resp.json()

    def test_pattern_test_endpoint(self, auth_client):
        resp = auth_client.post("/api/devices/hostname-rules/test/", {
            "pattern": r"-(crt|mdf)-",
            "hostnames": ["wco2-mdf-crt-01", "wco2-idf5-asw-01", "router1.local"],
        }, format="json")
        assert resp.status_code == 200
        results = {r["hostname"]: r["matches"] for r in resp.json()}
        assert results["wco2-mdf-crt-01"] is True
        assert results["router1.local"] is False

    def test_pattern_test_invalid_regex(self, auth_client):
        resp = auth_client.post("/api/devices/hostname-rules/test/", {
            "pattern": r"[bad", "hostnames": ["x"],
        }, format="json")
        assert resp.status_code == 400

    def test_per_device_apply(self, auth_client, core_role):
        from apps.devices.models import HostnameRule
        _make_rule(name="core", pattern=r"-crt-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20)
        dev = Device.objects.create(hostname="x-crt-1", ip_address="10.9.0.6")
        resp = auth_client.post(f"/api/devices/{dev.id}/apply-rules/", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["role_assigned"] is True
        assert resp.json()["device"]["role"]["id"] == core_role.id

    def test_bulk_apply(self, auth_client, core_role, wco2_site):
        from apps.devices.models import HostnameRule
        _make_rule(name="site", pattern=r"^wco2-", rule_type=HostnameRule.RuleType.SITE,
                   site=wco2_site, priority=10)
        _make_rule(name="core", pattern=r"-crt-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20)
        Device.objects.create(hostname="wco2-mdf-crt-01", ip_address="10.9.0.7")
        Device.objects.create(hostname="wco2-mdf-crt-02", ip_address="10.9.0.8")
        Device.objects.create(hostname="nomatch-host", ip_address="10.9.0.9")
        resp = auth_client.post("/api/devices/apply-rules/", {}, format="json")
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2
        assert resp.json()["skipped"] == 1

    def test_preview(self, auth_client, core_role, fw_role, wco2_site):
        from apps.devices.models import HostnameRule
        _make_rule(name="site", pattern=r"^wco2-", rule_type=HostnameRule.RuleType.SITE,
                   site=wco2_site, priority=10)
        _make_rule(name="core", pattern=r"-crt-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20)
        d1 = Device.objects.create(hostname="wco2-mdf-crt-01", ip_address="10.9.0.7")
        Device.objects.create(hostname="nomatch-host", ip_address="10.9.0.9")
        # Already has a role → role blocked, but site still applies → updated.
        Device.objects.create(hostname="wco2-edge-crt-9", ip_address="10.9.0.10", role=fw_role)

        resp = auth_client.post("/api/devices/hostname-rules/preview/", {}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == {"total_devices": 3, "would_update": 2, "would_skip": 1}

        upd = {u["hostname"]: u for u in body["would_update"]}
        assert upd["wco2-mdf-crt-01"]["new_role"]["id"] == core_role.id
        assert upd["wco2-mdf-crt-01"]["new_role"]["color"] == core_role.color
        assert upd["wco2-mdf-crt-01"]["new_site"]["name"] == "WCO2"
        assert upd["wco2-mdf-crt-01"]["current_role"] is None

        skip = {s["hostname"]: s["reason"] for s in body["would_skip"]}
        assert skip["nomatch-host"] == "no matching rules"

        # Preview must NOT save anything.
        d1.refresh_from_db()
        assert d1.role_id is None and d1.site_id is None

    def test_preview_skip_reason_role_already_assigned(self, auth_client, core_role, fw_role):
        from apps.devices.models import HostnameRule
        _make_rule(name="core", pattern=r"-crt-", rule_type=HostnameRule.RuleType.ROLE,
                   role=core_role, priority=20)
        Device.objects.create(hostname="x-crt-1", ip_address="10.9.0.11", role=fw_role)
        resp = auth_client.post("/api/devices/hostname-rules/preview/", {}, format="json")
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["would_update"] == 0
        assert body["would_skip"][0]["reason"] == "role already assigned"

    def test_seed_hostname_rules_idempotent(self, core_role, wco2_site):
        from django.core.management import call_command
        from apps.devices.models import HostnameRule
        call_command("seed_hostname_rules")
        first = HostnameRule.objects.count()
        assert first > 0
        # All seeded examples ship disabled.
        assert HostnameRule.objects.filter(enabled=True).count() == 0
        call_command("seed_hostname_rules")
        assert HostnameRule.objects.count() == first


class TestDeviceComplianceColumn:
    def _result(self, device, score, when=None):
        # The device list now reads the WEIGHTED DeviceComplianceScore (one per
        # device); calling this twice updates the same row (latest wins).
        from django.utils import timezone
        from apps.compliance.models import DeviceComplianceScore
        obj, _ = DeviceComplianceScore.objects.update_or_create(
            device=device,
            defaults={"score": score, "grade": "", "checked_at": when or timezone.now()})
        return obj

    @staticmethod
    def _rows(resp):
        body = resp.json()
        return body["results"] if isinstance(body, dict) else body

    def test_list_includes_score_and_grade(self, auth_client):
        from apps.devices.models import Device
        d = Device.objects.create(hostname="cx1", ip_address="10.7.0.1")
        self._result(d, 82.0)
        Device.objects.create(hostname="cx2", ip_address="10.7.0.2")  # no score
        by = {r["hostname"]: r for r in self._rows(auth_client.get("/api/devices/"))}
        assert by["cx1"]["compliance_score"] == 82 and by["cx1"]["compliance_grade"] == "B"
        assert by["cx2"]["compliance_score"] is None and by["cx2"]["compliance_grade"] is None

    def test_latest_result_wins(self, auth_client):
        from datetime import timedelta
        from django.utils import timezone
        from apps.devices.models import Device
        d = Device.objects.create(hostname="cx1", ip_address="10.7.0.1")
        self._result(d, 40.0, timezone.now() - timedelta(days=2))
        self._result(d, 95.0, timezone.now())
        by = {r["hostname"]: r for r in self._rows(auth_client.get("/api/devices/"))}
        assert by["cx1"]["compliance_score"] == 95 and by["cx1"]["compliance_grade"] == "A"

    def test_filter_by_grade(self, auth_client):
        from apps.devices.models import Device
        a = Device.objects.create(hostname="dev-a", ip_address="10.7.0.1"); self._result(a, 95.0)
        f = Device.objects.create(hostname="dev-f", ip_address="10.7.0.2"); self._result(f, 50.0)
        rows = self._rows(auth_client.get("/api/devices/?compliance_grade=F"))
        assert {r["hostname"] for r in rows} == {"dev-f"}

    def test_filter_not_checked(self, auth_client):
        from apps.devices.models import Device
        a = Device.objects.create(hostname="dev-a", ip_address="10.7.0.1"); self._result(a, 95.0)
        Device.objects.create(hostname="dev-n", ip_address="10.7.0.2")  # unchecked
        rows = self._rows(auth_client.get("/api/devices/?compliance_checked=false"))
        assert {r["hostname"] for r in rows} == {"dev-n"}

    def test_ordering_by_score_desc(self, auth_client):
        from apps.devices.models import Device
        a = Device.objects.create(hostname="dev-a", ip_address="10.7.0.1"); self._result(a, 60.0)
        b = Device.objects.create(hostname="dev-b", ip_address="10.7.0.2"); self._result(b, 90.0)
        rows = self._rows(auth_client.get("/api/devices/?ordering=-compliance_score"))
        scored = [r["hostname"] for r in rows if r["compliance_score"] is not None]
        assert scored[:2] == ["dev-b", "dev-a"]
