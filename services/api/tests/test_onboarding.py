"""Onboarding (Get Started wizard) gating — /api/onboarding/."""
import pytest

from apps.core.models import UserPreferences
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


class TestOnboardingStatus:
    def test_empty_system_shows_onboarding(self, auth_client, user):
        resp = auth_client.get("/api/onboarding/status/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["show_onboarding"] is True
        assert body["reasons"] == {"has_devices": False, "user_completed": False}

    def test_devices_present_hides_onboarding(self, auth_client):
        Device.objects.create(hostname="sw1", ip_address="10.0.0.1")
        body = auth_client.get("/api/onboarding/status/").json()
        assert body["show_onboarding"] is False
        assert body["reasons"]["has_devices"] is True

    def test_completed_user_hides_onboarding(self, auth_client, user):
        UserPreferences.for_user(user).save()  # ensure row
        prefs = UserPreferences.for_user(user)
        prefs.onboarding_completed = True
        prefs.save()
        body = auth_client.get("/api/onboarding/status/").json()
        assert body["show_onboarding"] is False
        assert body["reasons"] == {"has_devices": False, "user_completed": True}

    def test_requires_auth(self, api_client):
        assert api_client.get("/api/onboarding/status/").status_code in (401, 403)


class TestOnboardingComplete:
    def test_complete_sets_flag_and_hides(self, auth_client, user):
        resp = auth_client.post("/api/onboarding/complete/")
        assert resp.status_code == 200
        assert resp.json() == {"onboarding_completed": True}
        assert UserPreferences.for_user(user).onboarding_completed is True
        # Now hidden even though the system is still empty.
        body = auth_client.get("/api/onboarding/status/").json()
        assert body["show_onboarding"] is False

    def test_complete_is_idempotent(self, auth_client, user):
        auth_client.post("/api/onboarding/complete/")
        resp = auth_client.post("/api/onboarding/complete/")
        assert resp.status_code == 200
        assert resp.json()["onboarding_completed"] is True

    def test_requires_auth(self, api_client):
        assert api_client.post("/api/onboarding/complete/").status_code in (401, 403)
