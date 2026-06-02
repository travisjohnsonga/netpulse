"""Integration: security posture — anonymous rejection + no secret leakage."""
import logging

import pytest

from apps.credentials.models import CredentialProfile

pytestmark = pytest.mark.django_db

# Authenticated, write-capable endpoints that must reject anonymous access.
PROTECTED_ENDPOINTS = [
    "/api/devices/",
    "/api/devices/sites/",
    "/api/credentials/",
    "/api/checks/",
    "/api/alerts/rules/",
    "/api/alerting/teams/",
    "/api/devices/discovery/jobs/",
    "/api/configbackup/configs/",
    "/api/logs/",
]


class TestAnonymousRejection:
    @pytest.mark.parametrize("path", PROTECTED_ENDPOINTS)
    def test_endpoint_requires_auth(self, api_client, path):
        assert api_client.get(path).status_code == 401


class TestNoSecretLeakage:
    def test_password_not_logged_on_credential_create(self, auth_client, caplog):
        secret = "do-not-log-this-pw-9876"
        with caplog.at_level(logging.DEBUG):
            resp = auth_client.post(
                "/api/credentials/",
                {"name": "LeakCheck", "ssh_enabled": True, "ssh_username": "u",
                 "ssh_auth_method": "password", "ssh_password": secret},
                format="json",
            )
        assert resp.status_code == 201, resp.content
        # Best-effort: the secret must not appear in any captured log record.
        assert secret not in caplog.text
        # Nor persisted as a plaintext model attribute.
        profile = CredentialProfile.objects.get(pk=resp.json()["id"])
        assert not hasattr(profile, "ssh_password")
