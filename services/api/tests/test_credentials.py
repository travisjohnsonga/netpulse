import pytest

from apps.credentials.models import CredentialProfile, DeviceCredential
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def device():
    return Device.objects.create(hostname="core-rtr-01", ip_address="10.0.0.1")


@pytest.fixture
def snmp_profile():
    return CredentialProfile.objects.create(
        name="DC SNMPv2c",
        credential_type=CredentialProfile.CredentialType.SNMPV2C,
        snmp_version=CredentialProfile.SNMPVersion.V2C,
        vault_path="netpulse/credentials/seed",
    )


# ── CredentialProfile CRUD ──────────────────────────────────────────────────


class TestCredentialProfileCrud:
    def test_create_derives_vault_path_and_hides_secret(self, auth_client):
        resp = auth_client.post("/api/credentials/", {
            "name": "Branch SSH",
            "credential_type": "ssh_password",
            "username": "netadmin",
            "password": "super-secret",
        }, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        # Secret material is never echoed back.
        assert "password" not in body
        # vault_path is auto-derived from the pk and stored, not the secret.
        profile = CredentialProfile.objects.get(pk=body["id"])
        assert body["vault_path"] == f"netpulse/credentials/{profile.pk}"
        # The password is never persisted to the relational DB.
        assert not hasattr(profile, "password")

    def test_list_uses_lightweight_serializer(self, auth_client, snmp_profile):
        resp = auth_client.get("/api/credentials/")
        assert resp.status_code == 200
        item = resp.json()["results"][0]
        assert set(item.keys()) == {
            "id", "name", "credential_type", "username",
            "device_count", "last_tested", "last_test_result", "created_at",
        }

    def test_filter_by_type(self, auth_client, snmp_profile):
        CredentialProfile.objects.create(
            name="ssh", credential_type="ssh_key", vault_path="x")
        resp = auth_client.get("/api/credentials/?credential_type=snmpv2c")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert all(c["credential_type"] == "snmpv2c" for c in results)

    def test_device_count(self, auth_client, snmp_profile, device):
        DeviceCredential.objects.create(
            device=device, credential=snmp_profile, purpose="snmp_polling")
        resp = auth_client.get(f"/api/credentials/{snmp_profile.pk}/")
        assert resp.json()["device_count"] == 1

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/credentials/").status_code == 401

    def test_viewer_cannot_write(self, viewer_client):
        resp = viewer_client.post("/api/credentials/", {
            "name": "x", "credential_type": "ssh_password"}, format="json")
        assert resp.status_code == 403


# ── Test (probe) endpoint ────────────────────────────────────────────────────


class TestCredentialTestEndpoint:
    def test_requires_ip(self, auth_client, snmp_profile):
        resp = auth_client.post(f"/api/credentials/{snmp_profile.pk}/test/")
        assert resp.status_code == 400

    def test_records_outcome(self, auth_client, snmp_profile):
        # SNMP is UDP — sending a datagram to loopback succeeds instantly.
        resp = auth_client.post(
            f"/api/credentials/{snmp_profile.pk}/test/?ip=127.0.0.1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ip"] == "127.0.0.1"
        assert "success" in body and "message" in body
        snmp_profile.refresh_from_db()
        assert snmp_profile.last_tested is not None
        assert snmp_profile.last_test_result in ("success", "failure")

    def test_devices_action(self, auth_client, snmp_profile, device):
        DeviceCredential.objects.create(
            device=device, credential=snmp_profile, purpose="snmp_polling")
        resp = auth_client.get(f"/api/credentials/{snmp_profile.pk}/devices/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["device_hostname"] == "core-rtr-01"
        assert data[0]["purpose"] == "snmp_polling"


# ── Device-scoped association endpoints ──────────────────────────────────────


class TestDeviceCredentialEndpoints:
    def test_list_credentials_for_device(self, auth_client, device, snmp_profile):
        DeviceCredential.objects.create(
            device=device, credential=snmp_profile, purpose="snmp_polling")
        resp = auth_client.get(f"/api/devices/{device.pk}/credentials/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_associate_credential(self, auth_client, device, snmp_profile):
        resp = auth_client.post(f"/api/devices/{device.pk}/credentials/", {
            "credential": snmp_profile.pk,
            "purpose": "snmp_polling",
            "is_primary": True,
        }, format="json")
        assert resp.status_code == 201, resp.content
        assert DeviceCredential.objects.filter(
            device=device, purpose="snmp_polling").exists()

    def test_one_credential_per_purpose(self, auth_client, device, snmp_profile):
        DeviceCredential.objects.create(
            device=device, credential=snmp_profile, purpose="snmp_polling")
        resp = auth_client.post(f"/api/devices/{device.pk}/credentials/", {
            "credential": snmp_profile.pk, "purpose": "snmp_polling",
        }, format="json")
        assert resp.status_code == 400  # unique_together violation

    def test_delete_by_purpose(self, auth_client, device, snmp_profile):
        DeviceCredential.objects.create(
            device=device, credential=snmp_profile, purpose="snmp_polling")
        resp = auth_client.delete(
            f"/api/devices/{device.pk}/credentials/snmp_polling/")
        assert resp.status_code == 204
        assert not DeviceCredential.objects.filter(device=device).exists()

    def test_delete_unknown_purpose_404(self, auth_client, device):
        resp = auth_client.delete(
            f"/api/devices/{device.pk}/credentials/gnmi/")
        assert resp.status_code == 404
