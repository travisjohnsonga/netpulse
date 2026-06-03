import pytest

from apps.credentials.models import CredentialProfile
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def device():
    return Device.objects.create(hostname="core-rtr-01", ip_address="10.0.0.1")


@pytest.fixture
def ssh_profile():
    return CredentialProfile.objects.create(
        name="Cisco Standard",
        ssh_enabled=True,
        ssh_username="netadmin",
        ssh_auth_method="password",
        vault_path="netpulse/credentials/seed",
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────


class TestCredentialProfileCrud:
    def test_create_multi_protocol_hides_secrets(self, auth_client):
        resp = auth_client.post("/api/credentials/", {
            "name": "Cisco Full",
            "ssh_enabled": True,
            "ssh_username": "admin",
            "ssh_auth_method": "password",
            "ssh_password": "s3cret",
            "snmpv3_enabled": True,
            "snmpv3_username": "snmpuser",
            "snmpv3_auth_key": "authkey123",
            "snmpv3_priv_key": "privkey123",
        }, format="json")
        assert resp.status_code == 201, resp.content
        body = resp.json()
        # No secret echoed back.
        for f in ("ssh_password", "snmpv3_auth_key", "snmpv3_priv_key"):
            assert f not in body
        assert set(body["enabled_protocols"]) == {"ssh", "snmpv3"}
        profile = CredentialProfile.objects.get(pk=body["id"])
        assert body["vault_path"] == f"netpulse/credentials/{profile.pk}"
        assert not hasattr(profile, "ssh_password")

    def test_snmpv3_short_key_rejected(self, auth_client):
        resp = auth_client.post("/api/credentials/", {
            "name": "ShortKey", "snmpv3_enabled": True, "snmpv3_username": "u",
            "snmpv3_auth_key": "short",   # 5 chars < 8
            "snmpv3_priv_key": "privkey1234",
        }, format="json")
        assert resp.status_code == 400
        assert "snmpv3_auth_key" in resp.json()
        assert "8-64" in resp.json()["snmpv3_auth_key"][0]

    def test_snmpv3_long_key_rejected(self, auth_client):
        resp = auth_client.post("/api/credentials/", {
            "name": "LongKey", "snmpv3_enabled": True, "snmpv3_username": "u",
            "snmpv3_auth_key": "a" * 65,   # > 64
        }, format="json")
        assert resp.status_code == 400
        assert "snmpv3_auth_key" in resp.json()

    def test_snmpv3_valid_30_char_key_accepted(self, auth_client):
        # The real AOS-CX passphrase length that previously "worked".
        resp = auth_client.post("/api/credentials/", {
            "name": "GoodKey", "snmpv3_enabled": True, "snmpv3_username": "fpsrw",
            "snmpv3_auth_key": "xZQm2BEyZy1I0q1lJAQziedda8mT4u",   # 30 chars
            "snmpv3_priv_key": "2ETsOc5RMX0pg9T8nDwsbcxfOE2Srr",   # 30 chars
        }, format="json")
        assert resp.status_code == 201, resp.content

    def test_list_uses_lightweight_serializer(self, auth_client, ssh_profile):
        resp = auth_client.get("/api/credentials/")
        assert resp.status_code == 200
        item = resp.json()["results"][0]
        assert set(item.keys()) == {
            "id", "name", "enabled_protocols", "device_count",
            "last_tested", "last_test_result", "created_at",
        }
        assert item["enabled_protocols"] == ["ssh"]

    def test_filter_by_protocol(self, auth_client, ssh_profile):
        CredentialProfile.objects.create(name="snmp-only", snmpv2c_enabled=True, vault_path="x")
        resp = auth_client.get("/api/credentials/?ssh_enabled=true")
        assert resp.status_code == 200
        names = [c["name"] for c in resp.json()["results"]]
        assert names == ["Cisco Standard"]

    def test_device_count(self, auth_client, ssh_profile, device):
        device.credential_profile = ssh_profile
        device.save()
        resp = auth_client.get(f"/api/credentials/{ssh_profile.pk}/")
        assert resp.json()["device_count"] == 1

    def test_viewer_cannot_write(self, viewer_client):
        resp = viewer_client.post("/api/credentials/", {"name": "x", "ssh_enabled": True}, format="json")
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/credentials/").status_code == 401


# ── Test (probe) endpoint ────────────────────────────────────────────────────


class TestCredentialTestEndpoint:
    def test_requires_ip(self, auth_client, ssh_profile):
        resp = auth_client.post(f"/api/credentials/{ssh_profile.pk}/test/")
        assert resp.status_code == 400

    def test_no_protocols_enabled(self, auth_client):
        p = CredentialProfile.objects.create(name="empty", vault_path="x")
        resp = auth_client.post(f"/api/credentials/{p.pk}/test/?ip=127.0.0.1")
        assert resp.status_code == 400

    def test_per_protocol_results(self, auth_client):
        p = CredentialProfile.objects.create(
            name="multi", ssh_enabled=True, snmpv2c_enabled=True, vault_path="x")
        resp = auth_client.post(f"/api/credentials/{p.pk}/test/?ip=127.0.0.1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["overall"] in ("success", "partial", "failure")
        protos = {r["protocol"] for r in body["results"]}
        assert protos == {"ssh", "snmpv2c"}
        p.refresh_from_db()
        assert p.last_tested is not None

    def test_devices_action(self, auth_client, ssh_profile, device):
        device.credential_profile = ssh_profile
        device.save()
        resp = auth_client.get(f"/api/credentials/{ssh_profile.pk}/devices/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1 and data[0]["hostname"] == "core-rtr-01"


# ── Device assignment ────────────────────────────────────────────────────────


class TestDeviceAssignment:
    def test_assign_profile_to_device(self, auth_client, device, ssh_profile):
        resp = auth_client.patch(
            f"/api/devices/{device.pk}/", {"credential_profile": ssh_profile.pk}, format="json")
        assert resp.status_code == 200, resp.content
        device.refresh_from_db()
        assert device.credential_profile_id == ssh_profile.pk

    def test_unassign_profile(self, auth_client, device, ssh_profile):
        device.credential_profile = ssh_profile
        device.save()
        resp = auth_client.patch(
            f"/api/devices/{device.pk}/", {"credential_profile": None}, format="json")
        assert resp.status_code == 200, resp.content
        device.refresh_from_db()
        assert device.credential_profile_id is None
