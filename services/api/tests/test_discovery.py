"""Discovery API: DiscoveryJob CRUD + DiscoveredDevice approve/reject."""
import pytest

from apps.devices.models import Device, DiscoveredDevice, DiscoveryJob

pytestmark = pytest.mark.django_db


@pytest.fixture
def job(db):
    return DiscoveryJob.objects.create(
        name="DC-1 scan", method="scan",
        subnets=["10.1.0.0/24"], allowed_subnets=["10.0.0.0/8"],
        excluded_subnets=["10.99.0.0/16"],
    )


@pytest.fixture
def discovered(job):
    return DiscoveredDevice.objects.create(
        job=job, source_ip="10.1.0.5", confidence_score=60,
        discovered_hostname="rtr-5", discovered_vendor="cisco",
        discovered_platform="ios_xe", discovered_os="17.9",
        detection_methods=["snmp"], responds_to={"snmp": True},
    )


class TestDiscoveryJobCRUD:
    def test_create_job_sets_created_by_and_pending_status(self, auth_client):
        resp = auth_client.post("/api/devices/discovery/jobs/", {
            "name": "Topology walk", "method": "topology",
            "allowed_subnets": ["10.0.0.0/8"], "excluded_subnets": ["10.99.0.0/16"],
        }, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["status"] == "pending"
        assert body["created_by"] is not None
        job = DiscoveryJob.objects.get(pk=body["id"])
        assert job.created_by is not None

    def test_status_is_read_only_on_create(self, auth_client):
        resp = auth_client.post("/api/devices/discovery/jobs/", {
            "name": "x", "method": "scan", "status": "completed",
        }, format="json")
        assert resp.status_code == 201
        assert resp.json()["status"] == "pending"

    def test_list_jobs(self, auth_client, job):
        resp = auth_client.get("/api/devices/discovery/jobs/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_filter_jobs_by_method(self, auth_client, job):
        DiscoveryJob.objects.create(name="passive", method="passive")
        resp = auth_client.get("/api/devices/discovery/jobs/?method=scan")
        assert all(j["method"] == "scan" for j in resp.json()["results"])

    def test_delete_job(self, auth_client, job):
        resp = auth_client.delete(f"/api/devices/discovery/jobs/{job.pk}/")
        assert resp.status_code == 204
        assert not DiscoveryJob.objects.filter(pk=job.pk).exists()

    def test_pending_count_field(self, auth_client, job, discovered):
        resp = auth_client.get(f"/api/devices/discovery/jobs/{job.pk}/")
        assert resp.json()["pending_count"] == 1

    def test_nested_discovered_action(self, auth_client, job, discovered):
        resp = auth_client.get(f"/api/devices/discovery/jobs/{job.pk}/discovered/")
        assert resp.status_code == 200
        assert resp.json()[0]["source_ip"] == "10.1.0.5"

    def test_routing_does_not_shadow_device_detail(self, auth_client):
        """/api/devices/discovery/jobs/ must not be caught by the device detail route."""
        resp = auth_client.get("/api/devices/discovery/jobs/")
        assert resp.status_code == 200  # not a 404 device-detail miss

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/devices/discovery/jobs/").status_code == 401


class TestDiscoveredDeviceApproval:
    def test_list_filtered_by_status(self, auth_client, discovered):
        resp = auth_client.get("/api/devices/discovery/discovered/?status=pending")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_approve_creates_active_device(self, auth_client, discovered):
        resp = auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/approve/")
        assert resp.status_code == 201, resp.content
        device = Device.objects.get(ip_address="10.1.0.5")
        assert device.status == "active"
        assert device.hostname == "rtr-5"
        assert device.platform == "ios_xe"
        assert device.vendor == "cisco"
        discovered.refresh_from_db()
        assert discovered.status == "approved"
        assert discovered.approved_device_id == device.id
        assert discovered.approved_by is not None

    def test_approve_twice_resolves_to_existing(self, auth_client, discovered):
        # Second approve finds the device created by the first → already_exists,
        # not an error.
        auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/approve/")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/approve/")
        assert resp.status_code == 200
        assert resp.json()["already_exists"] is True
        assert Device.objects.filter(ip_address="10.1.0.5").count() == 1

    def test_approve_existing_ip_resolves_gracefully(self, auth_client, discovered):
        existing = Device.objects.create(hostname="existing", ip_address="10.1.0.5")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/approve/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["already_exists"] is True
        assert body["device"]["id"] == existing.id
        # No duplicate created, candidate linked to the existing device.
        assert Device.objects.filter(ip_address="10.1.0.5").count() == 1
        discovered.refresh_from_db()
        assert discovered.status == "approved"
        assert discovered.approved_device_id == existing.id

    def test_approve_existing_hostname_resolves_gracefully(self, auth_client, job):
        existing = Device.objects.create(hostname="rtr-5", ip_address="10.9.9.9")
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.1.0.7", discovered_hostname="rtr-5")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{dd.pk}/approve/")
        assert resp.status_code == 200
        assert resp.json()["device"]["id"] == existing.id
        assert not Device.objects.filter(ip_address="10.1.0.7").exists()

    def test_approve_unknown_platform_falls_back_to_other(self, auth_client, job):
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.1.0.8", discovered_platform="bogus-os")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{dd.pk}/approve/")
        assert resp.status_code == 201
        assert Device.objects.get(ip_address="10.1.0.8").platform == "other"

    def test_approve_unknown_platform_known_vendor_uses_vendor_default(self, auth_client, job):
        # Fortinet device with no platform parsed → approve resolves to fortios
        # from the vendor default (no explicit platform supplied).
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.1.0.18", discovered_vendor="fortinet",
            discovered_platform="")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{dd.pk}/approve/")
        assert resp.status_code == 201
        assert Device.objects.get(ip_address="10.1.0.18").platform == "fortios"

    def test_approve_unknown_platform_cisco_needs_choice(self, auth_client, job):
        # Cisco is multi-platform → no vendor default; falls back to "other"
        # unless the operator supplies a platform.
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.1.0.19", discovered_vendor="cisco", discovered_platform="")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{dd.pk}/approve/")
        assert resp.status_code == 201
        assert Device.objects.get(ip_address="10.1.0.19").platform == "other"

    def test_approve_platform_override(self, auth_client, job):
        # Unknown-platform device: caller supplies the platform on approve.
        dd = DiscoveredDevice.objects.create(job=job, source_ip="10.1.0.9")
        resp = auth_client.post(
            f"/api/devices/discovery/discovered/{dd.pk}/approve/",
            {"platform": "nxos"}, format="json")
        assert resp.status_code == 201
        assert Device.objects.get(ip_address="10.1.0.9").platform == "nxos"

    def test_reject_marks_rejected_without_device(self, auth_client, discovered):
        resp = auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/reject/")
        assert resp.status_code == 200
        discovered.refresh_from_db()
        assert discovered.status == "rejected"
        assert not Device.objects.filter(ip_address="10.1.0.5").exists()


class TestDiscoveredAlreadyExists:
    def test_no_match_flags_false(self, auth_client, discovered):
        resp = auth_client.get("/api/devices/discovery/discovered/?status=pending")
        row = next(r for r in unwrap(resp.json()) if r["id"] == discovered.id)
        assert row["already_exists"] is False
        assert row["existing_device_id"] is None
        assert row["existing_device_hostname"] is None

    def test_ip_match_flags_existing(self, auth_client, discovered):
        dev = Device.objects.create(hostname="router1", ip_address="10.1.0.5",
                                    management_ip="10.1.0.5")
        resp = auth_client.get("/api/devices/discovery/discovered/")
        row = next(r for r in unwrap(resp.json()) if r["id"] == discovered.id)
        assert row["already_exists"] is True
        assert row["existing_device_id"] == dev.id
        assert row["existing_device_hostname"] == "router1"

    def test_hostname_match_flags_existing(self, auth_client, job):
        dev = Device.objects.create(hostname="core-1", ip_address="10.9.9.9")
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.1.0.30", discovered_hostname="Core-1")
        resp = auth_client.get("/api/devices/discovery/discovered/")
        row = next(r for r in unwrap(resp.json()) if r["id"] == dd.id)
        assert row["already_exists"] is True
        assert row["existing_device_id"] == dev.id


def unwrap(data):
    return data["results"] if isinstance(data, dict) and "results" in data else data


class TestDiscoveryCredentials:
    @pytest.fixture
    def profile(self, db):
        from apps.credentials.models import CredentialProfile
        return CredentialProfile.objects.create(name="Lab creds", snmpv2c_enabled=True)

    def test_create_job_with_credential_profile(self, auth_client, profile):
        resp = auth_client.post("/api/devices/discovery/jobs/", {
            "name": "scan w/ creds", "method": "scan",
            "subnets": ["10.1.0.0/24"], "credential_profile": profile.id,
        }, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["credential_profile"] == profile.id
        assert body["credential_profile_name"] == "Lab creds"
        assert DiscoveryJob.objects.get(pk=body["id"]).credential_profile_id == profile.id

    def test_approve_inherits_job_credential_profile(self, auth_client, profile):
        job = DiscoveryJob.objects.create(name="j", method="scan", credential_profile=profile)
        dd = DiscoveredDevice.objects.create(job=job, source_ip="10.1.0.20")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{dd.pk}/approve/")
        assert resp.status_code == 201, resp.content
        assert Device.objects.get(ip_address="10.1.0.20").credential_profile_id == profile.id

    def test_approve_explicit_credential_overrides_job(self, auth_client, profile):
        from apps.credentials.models import CredentialProfile
        other = CredentialProfile.objects.create(name="Other", ssh_enabled=True)
        job = DiscoveryJob.objects.create(name="j2", method="scan", credential_profile=profile)
        dd = DiscoveredDevice.objects.create(job=job, source_ip="10.1.0.21")
        resp = auth_client.post(
            f"/api/devices/discovery/discovered/{dd.pk}/approve/",
            {"credential_profile": other.id}, format="json")
        assert resp.status_code == 201, resp.content
        assert Device.objects.get(ip_address="10.1.0.21").credential_profile_id == other.id

    def test_approve_with_unknown_credential_is_rejected(self, auth_client):
        job = DiscoveryJob.objects.create(name="j3", method="scan")
        dd = DiscoveredDevice.objects.create(job=job, source_ip="10.1.0.22")
        resp = auth_client.post(
            f"/api/devices/discovery/discovered/{dd.pk}/approve/",
            {"credential_profile": 99999}, format="json")
        assert resp.status_code == 400
        assert not Device.objects.filter(ip_address="10.1.0.22").exists()


class TestDiscoveryProgress:
    def test_serializer_includes_progress_pct(self, auth_client, job):
        job.progress_current, job.progress_total = 45, 100
        job.progress_message = "Scanning 10.1.0.45... (45/100)"
        job.ips_scanned = 45
        job.save()
        resp = auth_client.get("/api/devices/discovery/jobs/")
        row = next(r for r in resp.json()["results"] if r["id"] == job.id)
        assert row["progress_pct"] == 45
        assert row["progress_current"] == 45
        assert row["progress_total"] == 100
        assert row["progress_message"].startswith("Scanning")

    def test_progress_pct_zero_when_no_total(self, auth_client, job):
        resp = auth_client.get("/api/devices/discovery/jobs/")
        row = next(r for r in resp.json()["results"] if r["id"] == job.id)
        assert row["progress_pct"] == 0

    def test_progress_pct_capped_at_100(self, auth_client, job):
        job.progress_current, job.progress_total = 120, 100
        job.save()
        resp = auth_client.get("/api/devices/discovery/jobs/")
        row = next(r for r in resp.json()["results"] if r["id"] == job.id)
        assert row["progress_pct"] == 100

    def test_progress_endpoint(self, auth_client, job):
        from django.utils import timezone
        job.status = "running"
        job.started_at = timezone.now()
        job.progress_current, job.progress_total = 30, 100
        job.ips_scanned, job.devices_found = 30, 2
        job.progress_message = "Scanning 10.1.0.30... (30/100)"
        job.save()
        resp = auth_client.get(f"/api/devices/discovery/jobs/{job.id}/progress/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"
        assert body["progress_pct"] == 30
        assert body["ips_scanned"] == 30
        assert body["devices_found"] == 2
        assert body["elapsed_seconds"] >= 0
        assert body["progress_message"].startswith("Scanning")

    def test_progress_fields_read_only_on_create(self, auth_client):
        resp = auth_client.post("/api/devices/discovery/jobs/", {
            "name": "x", "method": "scan", "progress_current": 99, "progress_total": 100,
        }, format="json")
        assert resp.status_code == 201
        assert DiscoveryJob.objects.get(pk=resp.json()["id"]).progress_current == 0


class TestDiscoveryAutorun:
    def test_run_discovery_invokes_command(self, monkeypatch):
        from apps.devices.views import DiscoveryJobViewSet
        job = DiscoveryJob.objects.create(name="j", method="scan")
        calls = {}

        def fake_call_command(name, **kwargs):
            calls["name"] = name
            calls["kwargs"] = kwargs
        monkeypatch.setattr("django.core.management.call_command", fake_call_command)
        DiscoveryJobViewSet._run_discovery(job.id)
        assert calls["name"] == "run_discovery"
        assert calls["kwargs"] == {"job": job.id}

    def test_run_discovery_marks_failed_on_error(self, monkeypatch):
        from apps.devices.views import DiscoveryJobViewSet
        job = DiscoveryJob.objects.create(name="j", method="scan")

        def boom(name, **kwargs):
            raise RuntimeError("scan blew up")
        monkeypatch.setattr("django.core.management.call_command", boom)
        DiscoveryJobViewSet._run_discovery(job.id)
        job.refresh_from_db()
        assert job.status == "failed"
        assert "scan blew up" in job.progress_message
        assert "scan blew up" in job.error_message

    def test_start_discovery_schedules_scan_when_enabled(self, monkeypatch, settings):
        from apps.devices.views import DiscoveryJobViewSet
        settings.DISCOVERY_AUTORUN = True
        scheduled = []
        monkeypatch.setattr("django.db.transaction.on_commit", lambda cb: scheduled.append(cb))
        job = DiscoveryJob.objects.create(name="j", method="scan")
        DiscoveryJobViewSet._start_discovery(job)
        assert len(scheduled) == 1   # a worker was scheduled (not run — callback captured)

    def test_start_discovery_disabled_by_setting(self, monkeypatch, settings):
        from apps.devices.views import DiscoveryJobViewSet
        settings.DISCOVERY_AUTORUN = False
        scheduled = []
        monkeypatch.setattr("django.db.transaction.on_commit", lambda cb: scheduled.append(cb))
        job = DiscoveryJob.objects.create(name="j", method="scan")
        DiscoveryJobViewSet._start_discovery(job)
        assert scheduled == []

    def test_start_discovery_skips_passive(self, monkeypatch, settings):
        from apps.devices.views import DiscoveryJobViewSet
        settings.DISCOVERY_AUTORUN = True
        scheduled = []
        monkeypatch.setattr("django.db.transaction.on_commit", lambda cb: scheduled.append(cb))
        job = DiscoveryJob.objects.create(name="passive", method="passive")
        DiscoveryJobViewSet._start_discovery(job)
        assert scheduled == []


class TestDiscoveryJobEdit:
    def test_patch_updates_editable_fields(self, auth_client, job):
        resp = auth_client.patch(f"/api/devices/discovery/jobs/{job.id}/", {
            "name": "renamed", "subnets": ["10.2.0.0/24"], "rate_limit_pps": 25,
        }, format="json")
        assert resp.status_code == 200, resp.content
        job.refresh_from_db()
        assert job.name == "renamed"
        assert job.subnets == ["10.2.0.0/24"]
        assert job.rate_limit_pps == 25

    def test_patch_credential_profile(self, auth_client, job):
        from apps.credentials.models import CredentialProfile
        prof = CredentialProfile.objects.create(name="Edit creds", snmpv2c_enabled=True)
        resp = auth_client.patch(f"/api/devices/discovery/jobs/{job.id}/",
                                 {"credential_profile": prof.id}, format="json")
        assert resp.status_code == 200
        job.refresh_from_db()
        assert job.credential_profile_id == prof.id

    def test_cannot_edit_running_job(self, auth_client, job):
        job.status = "running"
        job.save()
        resp = auth_client.patch(f"/api/devices/discovery/jobs/{job.id}/",
                                 {"name": "nope"}, format="json")
        assert resp.status_code == 400
        assert "running" in resp.json()["error"].lower()
        job.refresh_from_db()
        assert job.name != "nope"

    def test_status_stays_read_only_on_patch(self, auth_client, job):
        resp = auth_client.patch(f"/api/devices/discovery/jobs/{job.id}/",
                                 {"status": "completed"}, format="json")
        assert resp.status_code == 200
        job.refresh_from_db()
        assert job.status == "pending"


class TestDiscoveryJobRun:
    def test_run_resets_and_triggers(self, auth_client, monkeypatch):
        # _start_discovery is a no-op here (DISCOVERY_AUTORUN false in tests),
        # but run() must reset progress and re-pend the job.
        job = DiscoveryJob.objects.create(
            name="done", method="scan", subnets=["10.1.0.0/24"],
            status="completed", progress_current=10, progress_total=10,
            ips_scanned=10, devices_found=2, progress_message="Complete",
        )
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.id}/run/")
        assert resp.status_code == 200, resp.content
        job.refresh_from_db()
        assert job.status == "pending"
        assert job.progress_current == 0 and job.ips_scanned == 0 and job.devices_found == 0
        assert job.progress_message == ""

    def test_run_blocked_when_running(self, auth_client):
        job = DiscoveryJob.objects.create(name="r", method="scan", status="running")
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.id}/run/")
        assert resp.status_code == 400

    def test_run_rejects_passive(self, auth_client):
        job = DiscoveryJob.objects.create(name="p", method="passive")
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.id}/run/")
        assert resp.status_code == 400

    def test_restart_resets_and_repends(self, auth_client):
        job = DiscoveryJob.objects.create(
            name="c", method="scan", subnets=["10.1.0.0/24"], status="cancelled",
            progress_current=5, ips_scanned=5, cancel_requested=True,
            progress_message="Cancelled by user")
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.id}/restart/")
        assert resp.status_code == 200, resp.content
        job.refresh_from_db()
        assert job.status == "pending"
        assert job.cancel_requested is False
        assert job.progress_current == 0 and job.ips_scanned == 0


class TestDiscoveryJobCancel:
    def test_cancel_pending_is_immediate(self, auth_client, job):
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.id}/cancel/")
        assert resp.status_code == 200, resp.content
        job.refresh_from_db()
        assert job.status == "cancelled"
        assert job.cancel_requested is True
        assert job.progress_message == "Cancelled by user"

    def test_cancel_running_sets_flag_only(self, auth_client):
        job = DiscoveryJob.objects.create(name="r", method="scan", status="running")
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.id}/cancel/")
        assert resp.status_code == 200
        job.refresh_from_db()
        # Engine flips it to cancelled when it notices; the API just sets the flag.
        assert job.status == "running"
        assert job.cancel_requested is True

    def test_cancel_completed_is_rejected(self, auth_client):
        job = DiscoveryJob.objects.create(name="d", method="scan", status="completed")
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.id}/cancel/")
        assert resp.status_code == 400


class TestNmapParser:
    def test_parses_up_ipv4_hosts(self):
        from apps.devices.management.commands.run_discovery import parse_nmap_hosts
        xml = b"""<?xml version="1.0"?><nmaprun>
          <host><status state="up"/><address addr="192.168.98.100" addrtype="ipv4"/></host>
          <host><status state="down"/><address addr="192.168.98.101" addrtype="ipv4"/></host>
          <host><status state="up"/><address addr="192.168.98.152" addrtype="ipv4"/>
                <address addr="AA:BB:CC:DD:EE:FF" addrtype="mac"/></host>
        </nmaprun>"""
        assert parse_nmap_hosts(xml) == ["192.168.98.100", "192.168.98.152"]

    def test_bad_xml_returns_empty(self):
        from apps.devices.management.commands.run_discovery import parse_nmap_hosts
        assert parse_nmap_hosts(b"not xml") == []
        assert parse_nmap_hosts(b"") == []

    def test_parses_services(self):
        from apps.devices.management.commands.run_discovery import parse_nmap_services
        xml = b"""<?xml version="1.0"?><nmaprun><host>
          <ports>
            <port portid="22"><state state="open"/>
              <service name="ssh" product="Cisco SSH" version="1.25"/></port>
            <port portid="80"><state state="closed"/></port>
            <port portid="443"><state state="open"/>
              <service name="https"/></port>
          </ports></host></nmaprun>"""
        svc = parse_nmap_services(xml)
        assert set(svc.keys()) == {22, 443}
        assert svc[22]["product"] == "Cisco SSH"

    def test_services_bad_xml(self):
        from apps.devices.management.commands.run_discovery import parse_nmap_services
        assert parse_nmap_services(b"nope") == {}


class TestVendorMapping:
    def test_sysobjid_enterprise_to_vendor(self):
        from apps.devices.management.commands.run_discovery import _vendor_from_sysobjid
        assert _vendor_from_sysobjid("1.3.6.1.4.1.9.1.222") == "cisco"
        assert _vendor_from_sysobjid("1.3.6.1.4.1.12356.101.1") == "fortinet"
        assert _vendor_from_sysobjid("1.3.6.1.4.1.2636.1.1.1") == "juniper"
        assert _vendor_from_sysobjid("1.3.6.1.2.1.1.1.0") == ""

    def test_vendor_from_services(self):
        from apps.devices.management.commands.run_discovery import _vendor_from_services
        assert _vendor_from_services({22: {"product": "Cisco SSH", "extrainfo": ""}}) == "cisco"
        assert _vendor_from_services({443: {"product": "nginx", "extrainfo": ""}}) == ""

    def test_vendor_from_descr(self):
        from apps.devices.management.commands.run_discovery import _vendor_from_descr
        # FortiGate sysDescr rarely contains the literal "fortinet" — match the
        # FortiOS / FortiGate spellings too.
        assert _vendor_from_descr("FortiGate-60F v7.2.5") == "fortinet"
        assert _vendor_from_descr("FortiOS v7.4.1 build2463") == "fortinet"
        assert _vendor_from_descr("Fortinet FortiGate") == "fortinet"
        assert _vendor_from_descr("Cisco IOS Software") == "cisco"
        assert _vendor_from_descr("Juniper Networks, Inc.") == "juniper"
        assert _vendor_from_descr("Some unknown box") == ""

    def test_default_platform_for_vendor(self):
        from apps.devices.management.commands.run_discovery import default_platform_for_vendor
        assert default_platform_for_vendor("fortinet") == "fortios"
        assert default_platform_for_vendor("Fortinet") == "fortios"
        assert default_platform_for_vendor("paloalto") == "panos"
        assert default_platform_for_vendor("cisco") == ""   # multi-platform → operator picks
        assert default_platform_for_vendor("") == ""


class TestPlatformDetection:
    def test_platform_from_banner(self):
        from apps.devices.management.commands.run_discovery import _platform_from_banner
        assert _platform_from_banner("SSH-2.0-Cisco-1.25") == "ios_xe"
        assert _platform_from_banner("SSH-2.0-FortiSSH_1.0") == "fortios"
        assert _platform_from_banner("SSH-2.0-OpenSSH_8.9") == ""

    def test_platform_from_descr(self):
        from apps.devices.management.commands.run_discovery import _platform_from_descr
        # IOS-XE / IOS XE / IOSXE all → ios_xe, and must win over plain IOS.
        assert _platform_from_descr("Cisco IOS XE Software, Version 17.9") == "ios_xe"
        assert _platform_from_descr("Cisco IOS-XE Software [Cupertino]") == "ios_xe"
        assert _platform_from_descr("...C8000V... IOSXE ...17.09.04a") == "ios_xe"
        assert _platform_from_descr("Cisco IOS Software, Version 15.7") == "ios"
        assert _platform_from_descr("Cisco NX-OS(tm)") == "nxos"
        assert _platform_from_descr("Cisco IOS XR Software") == "ios_xr"
        assert _platform_from_descr("FortiGate-60F FortiOS v7.2") == "fortios"
        assert _platform_from_descr("Fortinet appliance") == "fortios"  # bare "Fortinet"
        assert _platform_from_descr("SonicWALL NSA 3700 SonicOS 7.0") == "sonicwall"
        assert _platform_from_descr("ArubaOS-CX FL.10.09") == "aos_cx"   # CX before generic Aruba
        assert _platform_from_descr("ArubaOS (MODEL: 7210), Version 8.10") == "aruba"

    def test_default_platform_for_new_vendors(self):
        from apps.devices.management.commands.run_discovery import default_platform_for_vendor
        assert default_platform_for_vendor("sonicwall") == "sonicwall"
        assert default_platform_for_vendor("aruba") == "aruba"


class TestRunnerCancellation:
    """
    Unit-test the runner's cancel control flow with its DB/network methods
    stubbed. (The real run_in_executor DB reads can't see a test-transaction
    job from a worker thread, and a real scan would hit the network — so we
    stub them and assert the control flow instead.)
    """

    def _run_with_stubs(self, job, *, cancel_returns):
        import asyncio
        from apps.devices.management.commands.run_discovery import DiscoveryRunner

        record = {"scanned": False, "statuses": [], "messages": []}

        async def _go():
            runner = DiscoveryRunner(job=job, community="public", rate_pps=10)
            calls = {"n": 0}

            async def fake_check_cancel():
                calls["n"] += 1
                return cancel_returns(calls["n"])

            async def fake_active_scan():
                record["scanned"] = True

            async def fake_set_status(s, error=""):
                record["statuses"].append(s)

            async def fake_set_progress(**kw):
                if kw.get("message"):
                    record["messages"].append(kw["message"])

            runner._check_cancel = fake_check_cancel
            runner._active_scan = fake_active_scan
            runner._set_status = fake_set_status
            runner._set_progress = fake_set_progress
            await runner.run()
        asyncio.run(_go())
        return record

    def test_run_honours_precancel(self, db):
        # cancel_requested before start → never scans, ends cancelled.
        job = DiscoveryJob.objects.create(name="x", method="scan", subnets=["10.1.0.0/24"])
        rec = self._run_with_stubs(job, cancel_returns=lambda n: True)
        assert rec["scanned"] is False
        assert rec["statuses"] == ["cancelled"]
        assert "Cancelled by user" in rec["messages"]

    def test_run_completes_when_not_cancelled(self, db):
        # Not cancelled → scans and completes.
        job = DiscoveryJob.objects.create(name="y", method="scan", subnets=["10.1.0.0/24"])
        rec = self._run_with_stubs(job, cancel_returns=lambda n: False)
        assert rec["scanned"] is True
        assert rec["statuses"] == ["running", "completed"]
