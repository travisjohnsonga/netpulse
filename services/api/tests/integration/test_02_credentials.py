"""Integration: credential profiles — create, secrets hidden, list, delete."""
import pytest

from apps.credentials.models import CredentialProfile

pytestmark = pytest.mark.django_db


class TestCredentialLifecycle:
    def test_create_does_not_echo_secrets(self, auth_client):
        resp = auth_client.post(
            "/api/credentials/",
            {
                "name": "Integration SSH+SNMPv3",
                "ssh_enabled": True,
                "ssh_username": "netadmin",
                "ssh_auth_method": "password",
                "ssh_password": "Sup3rRealPw-2f9a",
                "snmpv3_enabled": True,
                "snmpv3_username": "snmpuser",
                "snmpv3_auth_key": "RealAuthKey-8chr",
                "snmpv3_priv_key": "RealPrivKey-8chr",
            },
            format="json",
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        # Security rule: secret material is never returned by the API.
        for secret_field in ("ssh_password", "snmpv3_auth_key", "snmpv3_priv_key"):
            assert secret_field not in body, f"{secret_field} leaked in response"
        # And the secret string itself must not appear anywhere in the payload.
        blob = str(body)
        for secret_val in ("Sup3rRealPw-2f9a", "RealAuthKey-8chr", "RealPrivKey-8chr"):
            assert secret_val not in blob
        assert set(body["enabled_protocols"]) == {"ssh", "snmpv3"}
        # vault_path points at OpenBao, not a plaintext credential.
        profile = CredentialProfile.objects.get(pk=body["id"])
        assert body["vault_path"] == f"netpulse/credentials/{profile.pk}"

    def test_list_then_delete(self, auth_client):
        created = auth_client.post(
            "/api/credentials/",
            {"name": "DeleteMe", "ssh_enabled": True, "ssh_username": "x",
             "ssh_auth_method": "password", "ssh_password": "pw"},
            format="json",
        ).json()
        cid = created["id"]

        listing = auth_client.get("/api/credentials/")
        assert listing.status_code == 200
        names = [c["name"] for c in listing.json()["results"]]
        assert "DeleteMe" in names

        deleted = auth_client.delete(f"/api/credentials/{cid}/")
        assert deleted.status_code == 204
        assert not CredentialProfile.objects.filter(pk=cid).exists()

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/credentials/").status_code == 401
