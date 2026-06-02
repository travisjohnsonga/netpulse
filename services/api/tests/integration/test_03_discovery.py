"""Integration: discovery jobs CRUD + discovered-device already_exists flag."""
import pytest

from apps.devices.models import Device, DiscoveredDevice, DiscoveryJob

pytestmark = pytest.mark.django_db


@pytest.fixture
def job():
    return DiscoveryJob.objects.create(
        name="Integration scan", method="scan",
        subnets=["10.5.0.0/24"], allowed_subnets=["10.0.0.0/8"],
        excluded_subnets=["10.99.0.0/16"],
    )


class TestDiscoveryJobCRUD:
    def test_create_list_edit_cancel(self, auth_client):
        # Create — status forced to pending, created_by set from request user.
        created = auth_client.post(
            "/api/devices/discovery/jobs/",
            {"name": "DC walk", "method": "scan", "allowed_subnets": ["10.0.0.0/8"]},
            format="json",
        )
        assert created.status_code == 201, created.content
        jid = created.json()["id"]
        assert created.json()["status"] == "pending"
        assert created.json()["created_by"] is not None

        # List.
        listing = auth_client.get("/api/devices/discovery/jobs/")
        assert listing.status_code == 200
        assert any(j["id"] == jid for j in listing.json()["results"])

        # Edit (PATCH).
        edited = auth_client.patch(
            f"/api/devices/discovery/jobs/{jid}/",
            {"max_devices": 250}, format="json",
        )
        assert edited.status_code == 200
        assert edited.json()["max_devices"] == 250

        # Cancel — pending job is cancelled immediately.
        cancelled = auth_client.post(f"/api/devices/discovery/jobs/{jid}/cancel/")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"

    def test_cancel_rejects_terminal_job(self, auth_client, job):
        job.status = DiscoveryJob.Status.COMPLETED
        job.save(update_fields=["status"])
        resp = auth_client.post(f"/api/devices/discovery/jobs/{job.pk}/cancel/")
        assert resp.status_code == 400

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/devices/discovery/jobs/").status_code == 401


class TestDiscoveredAlreadyExists:
    def test_already_exists_true_when_device_matches(self, auth_client, job):
        # A device already in inventory at the same IP.
        Device.objects.create(hostname="existing-rtr", ip_address="10.5.0.10")
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.5.0.10", confidence_score=60,
            discovered_hostname="rtr-10", detection_methods=["snmp"],
        )
        resp = auth_client.get(f"/api/devices/discovery/discovered/{dd.pk}/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["already_exists"] is True
        assert body["existing_device_id"] is not None

    def test_already_exists_false_when_no_match(self, auth_client, job):
        dd = DiscoveredDevice.objects.create(
            job=job, source_ip="10.5.0.250", confidence_score=30,
            detection_methods=["route_table"],
        )
        resp = auth_client.get(f"/api/devices/discovery/discovered/{dd.pk}/")
        assert resp.status_code == 200
        assert resp.json()["already_exists"] is False

    def test_list_filtered_by_status(self, auth_client, job):
        DiscoveredDevice.objects.create(job=job, source_ip="10.5.0.5", confidence_score=10)
        resp = auth_client.get("/api/devices/discovery/discovered/?status=pending")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
