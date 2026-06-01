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

    def test_approve_twice_is_blocked(self, auth_client, discovered):
        auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/approve/")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/approve/")
        assert resp.status_code == 400

    def test_approve_existing_ip_is_blocked(self, auth_client, discovered):
        Device.objects.create(hostname="existing", ip_address="10.1.0.5")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/approve/")
        assert resp.status_code == 400
        assert "already exists" in resp.json()["error"]

    def test_approve_hostname_collision_is_suffixed(self, auth_client, job):
        Device.objects.create(hostname="rtr-5", ip_address="10.9.9.9")
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.1.0.7", discovered_hostname="rtr-5")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{dd.pk}/approve/")
        assert resp.status_code == 201
        assert Device.objects.filter(hostname="rtr-5-10.1.0.7").exists()

    def test_approve_unknown_platform_falls_back_to_other(self, auth_client, job):
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.1.0.8", discovered_platform="bogus-os")
        resp = auth_client.post(f"/api/devices/discovery/discovered/{dd.pk}/approve/")
        assert resp.status_code == 201
        assert Device.objects.get(ip_address="10.1.0.8").platform == "other"

    def test_reject_marks_rejected_without_device(self, auth_client, discovered):
        resp = auth_client.post(f"/api/devices/discovery/discovered/{discovered.pk}/reject/")
        assert resp.status_code == 200
        discovered.refresh_from_db()
        assert discovered.status == "rejected"
        assert not Device.objects.filter(ip_address="10.1.0.5").exists()


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
