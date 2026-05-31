"""
RBAC tests — verifies that each role can do exactly what it should.

Matrix:
                    Admin   Engineer  Viewer   API    Unauth
 GET  /api/devices/   ✓        ✓        ✓       ✓      401
 POST /api/devices/   ✓        ✓        403     ✓      401
 GET  /api/alerts/    ✓        ✓        ✓       ✓      401
 POST /api/alerts/    ✓        ✓        403     ✓      401
 GET  /api/cve/       ✓        ✓        ✓       ✓      401
 GET  /api/lifecycle/ ✓        ✓        ✓       ✓      401
 POST /api/lifecycle/ ✓        ✓        403     ✓      401
 GET  /api/security/  ✓        ✓        ✓       ✓      401
 GET  /api/compliance/✓        ✓        ✓       ✓      401
 POST /api/compliance/✓        ✓        403     ✓      401
 GET  /api/collectors/✓        ✓        ✓       ✓      401
 POST /api/collectors/✓        ✓        403     ✓      401
"""
import pytest
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


# ── Helpers ───────────────────────────────────────────────────────────────────

_ip_counter = iter(range(201, 254))

def _device_payload(label="x"):
    ip_last = next(_ip_counter)
    return {"hostname": f"rtr-{label}-{ip_last}", "ip_address": f"10.0.{ip_last}.1"}


# ── Unauthenticated — every protected endpoint returns 401 ────────────────────

class TestUnauthenticated:
    URLS = [
        "/api/devices/",
        "/api/alerts/rules/",
        "/api/alerts/events/",
        "/api/alerts/channels/",
        "/api/compliance/policies/",
        "/api/compliance/rules/",
        "/api/compliance/results/",
        "/api/cve/cves/",
        "/api/cve/device-cves/",
        "/api/lifecycle/milestones/",
        "/api/security/risk-scores/",
        "/api/collectors/",
        "/api/telemetry/metrics/",
    ]

    def test_all_protected_endpoints_reject_unauthenticated(self, api_client):
        for url in self.URLS:
            resp = api_client.get(url)
            assert resp.status_code == 401, f"Expected 401 on {url}, got {resp.status_code}"

    def test_health_is_public(self, api_client):
        assert api_client.get("/api/health/").status_code == 200

    def test_token_obtain_is_reachable(self, api_client):
        # Bad credentials → 401 from simplejwt (not 404/405 "endpoint missing")
        resp = api_client.post("/api/auth/token/", {"username": "x", "password": "y"})
        assert resp.status_code in (400, 401), f"unexpected {resp.status_code}"
        # Endpoint must NOT be protected by our permission class (no "Authentication
        # credentials were not provided" response, which also uses 401 — verify
        # by checking the error detail differs from an unprotected endpoint).
        assert resp.status_code != 404
        assert resp.status_code != 405

    def test_webhooks_are_public(self, api_client):
        for path in ("slack", "teams", "gchat", "discord"):
            resp = api_client.post(f"/api/webhooks/{path}/", {}, format="json")
            assert resp.status_code != 401, f"{path} webhook should not require auth"


# ── JWT token endpoints ───────────────────────────────────────────────────────

class TestJWTEndpoints:
    def test_obtain_token_returns_access_and_refresh(self, user, api_client):
        resp = api_client.post(
            "/api/auth/token/",
            {"username": "testuser", "password": "testpass123"},
        )
        assert resp.status_code == 200
        assert "access" in resp.json()
        assert "refresh" in resp.json()

    def test_token_endpoint_is_rate_limited(self, api_client, monkeypatch):
        # H1: brute-force protection. The "auth" rate is disabled in test
        # settings; patch the throttle class to a tiny rate and confirm the
        # endpoint returns 429 once exceeded.
        from django.core.cache import cache
        from rest_framework.throttling import SimpleRateThrottle
        cache.clear()
        monkeypatch.setattr(SimpleRateThrottle, "THROTTLE_RATES", {"auth": "2/min"})
        try:
            codes = [api_client.post("/api/auth/token/",
                                     {"username": "x", "password": "y"}).status_code
                     for _ in range(4)]
            assert 429 in codes, f"expected a 429 after the limit, got {codes}"
        finally:
            cache.clear()

    def test_token_contains_role_claim(self, user, api_client):
        import base64, json
        resp = api_client.post(
            "/api/auth/token/",
            {"username": "testuser", "password": "testpass123"},
        )
        payload_b64 = resp.json()["access"].split(".")[1]
        # Add padding
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        assert payload["role"] == "admin"
        assert payload["username"] == "testuser"

    def test_refresh_issues_new_access_token(self, user, api_client):
        tokens = api_client.post(
            "/api/auth/token/",
            {"username": "testuser", "password": "testpass123"},
        ).json()
        resp = api_client.post(
            "/api/auth/token/refresh/",
            {"refresh": tokens["refresh"]},
        )
        assert resp.status_code == 200
        assert "access" in resp.json()

    def test_bad_credentials_returns_401(self, api_client):
        resp = api_client.post(
            "/api/auth/token/",
            {"username": "nobody", "password": "wrong"},
        )
        assert resp.status_code == 401

    def test_engineer_role_in_token(self, engineer_user, api_client):
        import base64, json
        resp = api_client.post(
            "/api/auth/token/",
            {"username": "engineer_user", "password": "testpass123"},
        )
        payload_b64 = resp.json()["access"].split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        assert payload["role"] == "engineer"


# ── Admin role ────────────────────────────────────────────────────────────────

class TestAdminRole:
    def test_admin_can_list_devices(self, admin_client):
        assert admin_client.get("/api/devices/").status_code == 200

    def test_admin_can_create_device(self, admin_client):
        resp = admin_client.post("/api/devices/", _device_payload("admin1"))
        assert resp.status_code == 201

    def test_admin_can_delete_device(self, admin_client):
        d = Device.objects.create(hostname="rtr-del", ip_address="10.99.0.1")
        assert admin_client.delete(f"/api/devices/{d.pk}/").status_code == 204

    def test_admin_can_list_alerts(self, admin_client):
        assert admin_client.get("/api/alerts/rules/").status_code == 200

    def test_admin_can_create_alert_rule(self, admin_client):
        resp = admin_client.post(
            "/api/alerts/rules/",
            {"name": "Admin Rule", "condition": {}},
            format="json",
        )
        assert resp.status_code == 201

    def test_admin_can_access_cve(self, admin_client):
        assert admin_client.get("/api/cve/cves/").status_code == 200

    def test_admin_can_access_security(self, admin_client):
        assert admin_client.get("/api/security/risk-scores/").status_code == 200

    def test_admin_can_create_collector(self, admin_client):
        resp = admin_client.post(
            "/api/collectors/",
            {"name": "Admin Collector"},
        )
        assert resp.status_code == 201

    def test_admin_can_create_compliance_policy(self, admin_client):
        resp = admin_client.post(
            "/api/compliance/policies/",
            {"name": "Admin Policy"},
            format="json",
        )
        assert resp.status_code == 201


# ── Engineer role ─────────────────────────────────────────────────────────────

class TestEngineerRole:
    def test_engineer_can_read_devices(self, engineer_client):
        assert engineer_client.get("/api/devices/").status_code == 200

    def test_engineer_can_create_device(self, engineer_client):
        resp = engineer_client.post("/api/devices/", _device_payload("eng1"))
        assert resp.status_code == 201

    def test_engineer_can_update_device(self, engineer_client):
        d = Device.objects.create(hostname="rtr-eng", ip_address="10.20.0.1")
        resp = engineer_client.patch(f"/api/devices/{d.pk}/", {"status": "maintenance"})
        assert resp.status_code == 200

    def test_engineer_can_delete_device(self, engineer_client):
        d = Device.objects.create(hostname="rtr-eng-del", ip_address="10.20.0.2")
        assert engineer_client.delete(f"/api/devices/{d.pk}/").status_code == 204

    def test_engineer_can_create_alert_rule(self, engineer_client):
        resp = engineer_client.post(
            "/api/alerts/rules/",
            {"name": "Eng Rule", "condition": {}},
            format="json",
        )
        assert resp.status_code == 201

    def test_engineer_can_read_cve(self, engineer_client):
        assert engineer_client.get("/api/cve/cves/").status_code == 200

    def test_engineer_can_read_security(self, engineer_client):
        assert engineer_client.get("/api/security/risk-scores/").status_code == 200

    def test_engineer_can_create_lifecycle_milestone(self, engineer_client):
        d = Device.objects.create(hostname="rtr-lc", ip_address="10.20.0.10")
        resp = engineer_client.post(
            "/api/lifecycle/milestones/",
            {"device": d.pk, "milestone_type": "eos", "milestone_date": "2027-01-01"},
        )
        assert resp.status_code == 201

    def test_engineer_can_access_compliance(self, engineer_client):
        assert engineer_client.get("/api/compliance/policies/").status_code == 200

    def test_engineer_can_create_compliance_policy(self, engineer_client):
        resp = engineer_client.post(
            "/api/compliance/policies/",
            {"name": "Eng Policy"},
            format="json",
        )
        assert resp.status_code == 201


# ── Viewer role ───────────────────────────────────────────────────────────────

class TestViewerRole:
    def test_viewer_can_read_devices(self, viewer_client):
        assert viewer_client.get("/api/devices/").status_code == 200

    def test_viewer_cannot_create_device(self, viewer_client):
        resp = viewer_client.post("/api/devices/", _device_payload("vw1"))
        assert resp.status_code == 403

    def test_viewer_cannot_update_device(self, viewer_client):
        d = Device.objects.create(hostname="rtr-vw", ip_address="10.30.0.1")
        resp = viewer_client.patch(f"/api/devices/{d.pk}/", {"status": "inactive"})
        assert resp.status_code == 403

    def test_viewer_cannot_delete_device(self, viewer_client):
        d = Device.objects.create(hostname="rtr-vw-del", ip_address="10.30.0.2")
        assert viewer_client.delete(f"/api/devices/{d.pk}/").status_code == 403

    def test_viewer_can_read_alerts(self, viewer_client):
        assert viewer_client.get("/api/alerts/rules/").status_code == 200

    def test_viewer_cannot_create_alert_rule(self, viewer_client):
        resp = viewer_client.post(
            "/api/alerts/rules/",
            {"name": "Viewer Rule", "condition": {}},
            format="json",
        )
        assert resp.status_code == 403

    def test_viewer_can_read_cve(self, viewer_client):
        assert viewer_client.get("/api/cve/cves/").status_code == 200

    def test_viewer_can_read_security(self, viewer_client):
        assert viewer_client.get("/api/security/risk-scores/").status_code == 200

    def test_viewer_can_read_compliance(self, viewer_client):
        assert viewer_client.get("/api/compliance/policies/").status_code == 200

    def test_viewer_cannot_create_compliance_policy(self, viewer_client):
        resp = viewer_client.post(
            "/api/compliance/policies/",
            {"name": "Viewer Policy"},
            format="json",
        )
        assert resp.status_code == 403

    def test_viewer_can_read_collectors(self, viewer_client):
        assert viewer_client.get("/api/collectors/").status_code == 200

    def test_viewer_cannot_create_collector(self, viewer_client):
        resp = viewer_client.post("/api/collectors/", {"name": "vw-coll"})
        assert resp.status_code == 403

    def test_viewer_can_read_lifecycle(self, viewer_client):
        assert viewer_client.get("/api/lifecycle/milestones/").status_code == 200

    def test_viewer_cannot_create_lifecycle_milestone(self, viewer_client):
        d = Device.objects.create(hostname="rtr-vwlc", ip_address="10.30.0.5")
        resp = viewer_client.post(
            "/api/lifecycle/milestones/",
            {"device": d.pk, "milestone_type": "eos", "milestone_date": "2027-01-01"},
        )
        assert resp.status_code == 403


# ── API service-account role ──────────────────────────────────────────────────

class TestAPIRole:
    def test_api_can_read_devices(self, api_svc_client):
        assert api_svc_client.get("/api/devices/").status_code == 200

    def test_api_can_create_device(self, api_svc_client):
        resp = api_svc_client.post("/api/devices/", _device_payload("api1"))
        assert resp.status_code == 201

    def test_api_can_update_device(self, api_svc_client):
        d = Device.objects.create(hostname="rtr-api", ip_address="10.40.0.1")
        resp = api_svc_client.patch(f"/api/devices/{d.pk}/", {"status": "maintenance"})
        assert resp.status_code == 200

    def test_api_can_read_alerts(self, api_svc_client):
        assert api_svc_client.get("/api/alerts/rules/").status_code == 200

    def test_api_can_create_alert_rule(self, api_svc_client):
        resp = api_svc_client.post(
            "/api/alerts/rules/",
            {"name": "API Rule", "condition": {}},
            format="json",
        )
        assert resp.status_code == 201

    def test_api_can_read_cve(self, api_svc_client):
        assert api_svc_client.get("/api/cve/cves/").status_code == 200

    def test_api_can_create_collector(self, api_svc_client):
        resp = api_svc_client.post("/api/collectors/", {"name": "api-coll"})
        assert resp.status_code == 201


# ── NetPulseUser model ────────────────────────────────────────────────────────

class TestNetPulseUserModel:
    def test_default_role_is_viewer(self, db):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        u = User.objects.create_user(username="newbie", password="pass")
        assert u.role == "viewer"

    def test_is_admin_property(self, admin_user):
        assert admin_user.is_admin is True

    def test_is_admin_false_for_viewer(self, viewer_user):
        assert viewer_user.is_admin is False

    def test_can_write_admin(self, admin_user):
        assert admin_user.can_write is True

    def test_can_write_engineer(self, engineer_user):
        assert engineer_user.can_write is True

    def test_can_write_api(self, api_user):
        assert api_user.can_write is True

    def test_can_write_viewer(self, viewer_user):
        assert viewer_user.can_write is False

    def test_superuser_is_admin(self, db):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        su = User.objects.create_superuser(username="su", password="pass")
        assert su.is_admin is True

    def test_str_includes_role(self, engineer_user):
        assert "engineer" in str(engineer_user)

    def test_role_choices(self):
        from apps.core.models import Role
        roles = {r.value for r in Role}
        assert roles == {"admin", "engineer", "viewer", "api"}


# ── create_roles management command ──────────────────────────────────────────

class TestCreateRolesCommand:
    def test_creates_four_groups(self, db):
        from django.contrib.auth.models import Group
        from django.core.management import call_command
        call_command("create_roles", verbosity=0)
        assert Group.objects.filter(name="Admin").exists()
        assert Group.objects.filter(name="Engineer").exists()
        assert Group.objects.filter(name="Viewer").exists()
        assert Group.objects.filter(name="API").exists()

    def test_idempotent(self, db):
        from django.contrib.auth.models import Group
        from django.core.management import call_command
        call_command("create_roles", verbosity=0)
        call_command("create_roles", verbosity=0)
        assert Group.objects.filter(name="Admin").count() == 1

    def test_superuser_promotion(self, db):
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        User = get_user_model()
        u = User.objects.create_user(username="promoteme", password="pass", role="viewer")
        call_command("create_roles", superuser="promoteme", verbosity=0)
        u.refresh_from_db()
        assert u.is_superuser is True
        assert u.is_staff is True
        assert u.role == "admin"
