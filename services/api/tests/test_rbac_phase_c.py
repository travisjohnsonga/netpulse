"""RBAC Track 2 Phase C (part 1) — role-management API + anti-escalation guardrail.

Covers the capability catalog endpoint, role CRUD (custom roles only — system
roles are read-only), the load-bearing anti-escalation rule (you can't grant a
capability you don't hold), system-role/immutability protection, in-use deletion
protection, and the /users/me/ self-capabilities field.
"""
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.core import capabilities as caps
from apps.core.models import RBACRole

User = get_user_model()
pytestmark = pytest.mark.django_db

CATALOG = "/api/rbac/capabilities/"
ROLES = "/api/rbac/roles/"
ME = "/api/users/me/"


def client_for(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def admin(db):
    # role=admin → save() sync gives the admin RBACRole (ALL_CAPABILITIES).
    return User.objects.create_user(username="c_admin", password="x", role="admin")


@pytest.fixture
def role_manager(db):
    """Holds rbac:manage but NOT credential:manage (the escalation test subject)."""
    role = RBACRole.objects.create(name="role-manager", capabilities=[caps.RBAC_MANAGE])
    u = User.objects.create_user(username="c_rolemgr", password="x", role="viewer")
    u.rbac_role = role  # custom (non-system) → save() respects it
    u.save()
    return u


# ── Catalog ──────────────────────────────────────────────────────────────────

class TestCatalog:
    def test_grouped_and_complete(self, admin):
        resp = client_for(admin).get(CATALOG)
        assert resp.status_code == 200
        data = resp.json()
        assert all({"group", "capabilities"} <= set(g) for g in data)
        # Grouped by prefix; flattening recovers the whole catalog.
        flat = {c["name"] for g in data for c in g["capabilities"]}
        assert flat == set(caps.ALL_CAPABILITIES)
        groups = {g["group"] for g in data}
        assert {"device", "integration", "config", "chatops"} <= groups

    def test_requires_rbac_manage(self, engineer_user, viewer_user):
        for u in (engineer_user, viewer_user):
            assert client_for(u).get(CATALOG).status_code == 403

    def test_read_only(self, admin):
        assert client_for(admin).post(CATALOG, {}, format="json").status_code in (403, 405)


# ── Role CRUD (custom roles) ───────────────────────────────────────────────────

class TestCustomRoleCRUD:
    def test_admin_full_crud(self, admin):
        c = client_for(admin)
        r = c.post(ROLES, {"name": "noc", "description": "NOC",
                           "capabilities": [caps.DEVICE_VIEW, caps.ALERT_VIEW]}, format="json")
        assert r.status_code == 201, r.content
        rid = r.json()["id"]
        assert r.json()["is_system"] is False
        assert r.json()["user_count"] == 0
        assert c.get(f"{ROLES}{rid}/").status_code == 200
        # update caps
        assert c.patch(f"{ROLES}{rid}/", {"capabilities": [caps.DEVICE_VIEW]},
                       format="json").status_code == 200
        assert set(RBACRole.objects.get(pk=rid).capabilities) == {caps.DEVICE_VIEW}
        assert c.delete(f"{ROLES}{rid}/").status_code == 204
        assert not RBACRole.objects.filter(pk=rid).exists()

    def test_unknown_capability_is_400_not_500(self, admin):
        r = client_for(admin).post(ROLES, {"name": "bad", "capabilities": ["not:a:cap"]},
                                   format="json")
        assert r.status_code == 400

    def test_non_rbac_manage_user_blocked_everywhere(self, engineer_user):
        c = client_for(engineer_user)
        assert c.get(ROLES).status_code == 403
        assert c.post(ROLES, {"name": "x", "capabilities": []}, format="json").status_code == 403


# ── THE GUARDRAIL: anti-escalation ─────────────────────────────────────────────

class TestAntiEscalation:
    def test_admin_can_grant_anything(self, admin):
        r = client_for(admin).post(ROLES, {"name": "powerful",
                                           "capabilities": [caps.CREDENTIAL_MANAGE]}, format="json")
        assert r.status_code == 201

    def test_role_manager_can_grant_only_what_it_holds(self, role_manager):
        c = client_for(role_manager)
        # Can grant rbac:manage (it holds it).
        assert c.post(ROLES, {"name": "ok", "capabilities": [caps.RBAC_MANAGE]},
                      format="json").status_code == 201

    def test_role_manager_cannot_grant_unheld_cap_on_create(self, role_manager):
        r = client_for(role_manager).post(
            ROLES, {"name": "esc", "capabilities": [caps.CREDENTIAL_MANAGE]}, format="json")
        assert r.status_code == 403
        assert "credential:manage" in str(r.content)
        assert not RBACRole.objects.filter(name="esc").exists()

    def test_role_manager_cannot_grant_unheld_cap_on_update(self, admin, role_manager):
        # An existing custom role the role-manager will try to escalate.
        role = RBACRole.objects.create(name="target", capabilities=[caps.RBAC_MANAGE])
        r = client_for(role_manager).patch(
            f"{ROLES}{role.id}/", {"capabilities": [caps.RBAC_MANAGE, caps.CREDENTIAL_MANAGE]},
            format="json")
        assert r.status_code == 403
        role.refresh_from_db()
        assert caps.CREDENTIAL_MANAGE not in role.capabilities

    def test_role_manager_cannot_assign_role_with_unheld_caps(self, admin, role_manager):
        victim = User.objects.create_user(username="victim", password="x", role="viewer")
        powerful = RBACRole.objects.create(name="cred-role", capabilities=[caps.CREDENTIAL_MANAGE])
        r = client_for(role_manager).patch(
            f"/api/users/{victim.id}/rbac-role/", {"rbac_role_id": powerful.id}, format="json")
        assert r.status_code == 403
        victim.refresh_from_db()
        assert victim.rbac_role_id != powerful.id


# ── System-role read-only + immutability (amended: ALL system roles read-only) ──

class TestSystemRoleProtection:
    @pytest.mark.parametrize("name", ["superadmin", "admin", "engineer", "api", "viewer"])
    def test_system_roles_cannot_be_edited(self, admin, name):
        role = RBACRole.objects.get(name=name)
        r = client_for(admin).patch(f"{ROLES}{role.id}/", {"description": "hacked"},
                                    format="json")
        assert r.status_code == 403
        role.refresh_from_db()
        assert role.description != "hacked"

    @pytest.mark.parametrize("name", ["superadmin", "admin", "engineer", "api", "viewer"])
    def test_system_roles_cannot_be_deleted(self, admin, name):
        role = RBACRole.objects.get(name=name)
        assert client_for(admin).delete(f"{ROLES}{role.id}/").status_code == 403
        assert RBACRole.objects.filter(name=name).exists()


# ── In-use deletion protection ─────────────────────────────────────────────────

class TestInUseProtection:
    def test_custom_role_in_use_cannot_be_deleted(self, admin):
        role = RBACRole.objects.create(name="assigned", capabilities=[caps.DEVICE_VIEW])
        u = User.objects.create_user(username="holder", password="x", role="viewer")
        u.rbac_role = role
        u.save()
        r = client_for(admin).delete(f"{ROLES}{role.id}/")
        assert r.status_code == 409
        assert "1 user" in str(r.content)
        assert RBACRole.objects.filter(pk=role.id).exists()


# ── User-role assignment ───────────────────────────────────────────────────────

class TestUserRoleAssignment:
    def test_admin_assigns_custom_role(self, admin):
        role = RBACRole.objects.create(name="ops", capabilities=[caps.DEVICE_VIEW, caps.CHECK_VIEW])
        target = User.objects.create_user(username="t1", password="x", role="viewer")
        r = client_for(admin).patch(f"/api/users/{target.id}/rbac-role/",
                                    {"rbac_role_id": role.id}, format="json")
        assert r.status_code == 200
        target.refresh_from_db()
        assert target.rbac_role_id == role.id  # custom role sticks (save() respects it)

    def test_admin_assigns_system_role_aligns_legacy(self, admin):
        eng = RBACRole.objects.get(name="engineer")
        target = User.objects.create_user(username="t2", password="x", role="viewer")
        r = client_for(admin).patch(f"/api/users/{target.id}/rbac-role/",
                                    {"rbac_role_id": eng.id}, format="json")
        assert r.status_code == 200
        target.refresh_from_db()
        assert target.rbac_role.name == "engineer"
        assert target.role == "engineer"  # legacy field aligned for JWT/group consistency

    def test_assignment_requires_rbac_manage(self, engineer_user):
        role = RBACRole.objects.create(name="z", capabilities=[caps.DEVICE_VIEW])
        target = User.objects.create_user(username="t3", password="x", role="viewer")
        assert client_for(engineer_user).patch(
            f"/api/users/{target.id}/rbac-role/", {"rbac_role_id": role.id},
            format="json").status_code == 403


# ── /users/me/ self-capabilities ───────────────────────────────────────────────

class TestMeCapabilities:
    def test_engineer_sees_engineer_set(self, engineer_user):
        data = client_for(engineer_user).get(ME).json()
        assert data["capabilities"] == sorted(caps.ENGINEER_CAPABILITIES)
        assert data["rbac_role"] == {"name": "engineer", "is_system": True}

    def test_viewer_sees_viewer_set(self, viewer_user):
        assert client_for(viewer_user).get(ME).json()["capabilities"] == sorted(caps.VIEW_CAPABILITIES)

    def test_superuser_sees_full_catalog(self, db):
        su = User.objects.create_superuser(username="phc_su", password="x", email="s@x.io")
        assert client_for(su).get(ME).json()["capabilities"] == sorted(caps.ALL_CAPABILITIES)

    def test_no_role_sees_empty(self, db):
        u = User.objects.create_user(username="phc_norole", password="x")
        User.objects.filter(pk=u.pk).update(rbac_role=None)
        u.refresh_from_db()
        data = client_for(u).get(ME).json()
        assert data["capabilities"] == []
        assert data["rbac_role"] is None

    def test_capabilities_field_is_read_only(self, engineer_user):
        c = client_for(engineer_user)
        c.put(ME, {"capabilities": ["user:manage"], "email": "e@x.io"}, format="json")
        # Caps unchanged (method field ignored on input); the seeded engineer set stands.
        assert c.get(ME).json()["capabilities"] == sorted(caps.ENGINEER_CAPABILITIES)
