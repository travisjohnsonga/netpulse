"""Admin user management API: CRUD + delete/demote safety guards."""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

User = get_user_model()
pytestmark = pytest.mark.django_db


@pytest.fixture
def roles():
    # seed the role groups the serializer syncs against
    from django.core.management import call_command
    call_command("create_roles")


class TestUserListAndPermissions:
    def test_admin_can_list_users(self, admin_client, engineer_user):
        resp = admin_client.get("/api/users/")
        assert resp.status_code == 200
        usernames = {u["username"] for u in resp.json()["results"]}
        assert {"admin_user", "engineer_user"} <= usernames

    def test_engineer_cannot_list_users(self, engineer_client):
        resp = engineer_client.get("/api/users/")
        assert resp.status_code == 403

    def test_viewer_cannot_list_users(self, viewer_client):
        assert viewer_client.get("/api/users/").status_code == 403

    def test_unauthenticated_rejected(self, api_client):
        assert api_client.get("/api/users/").status_code == 401

    def test_users_me_still_resolves_not_shadowed_by_router(self, auth_client):
        """/users/me/ must hit MeView, not the UserViewSet detail route."""
        resp = auth_client.get("/api/users/me/")
        assert resp.status_code == 200
        assert resp.json()["username"] == "testuser"

    def test_password_never_returned(self, admin_client):
        resp = admin_client.get("/api/users/")
        for u in resp.json()["results"]:
            assert "password" not in u


class TestUserCreateUpdate:
    def test_create_user_sets_role_and_group(self, admin_client, roles):
        resp = admin_client.post("/api/users/", {
            "username": "newbie", "email": "n@x.io", "role": "engineer",
            "password": "Sup3rStr0ngPass!",
        }, format="json")
        assert resp.status_code == 201, resp.content
        user = User.objects.get(username="newbie")
        assert user.role == "engineer"
        assert user.check_password("Sup3rStr0ngPass!")
        assert user.groups.filter(name="Engineer").exists()

    def test_create_requires_password(self, admin_client, roles):
        resp = admin_client.post("/api/users/", {
            "username": "nopw", "role": "viewer",
        }, format="json")
        assert resp.status_code == 400
        assert "password" in resp.json()

    def test_create_rejects_weak_password(self, admin_client, roles):
        resp = admin_client.post("/api/users/", {
            "username": "weak", "role": "viewer", "password": "123",
        }, format="json")
        assert resp.status_code == 400

    def test_update_role_resyncs_group(self, admin_client, roles, engineer_user):
        resp = admin_client.patch(
            f"/api/users/{engineer_user.pk}/", {"role": "viewer"}, format="json")
        assert resp.status_code == 200
        engineer_user.refresh_from_db()
        assert engineer_user.role == "viewer"
        assert engineer_user.groups.filter(name="Viewer").exists()
        assert not engineer_user.groups.filter(name="Engineer").exists()


class TestDeleteGuards:
    def test_cannot_delete_self(self, admin_client, admin_user):
        resp = admin_client.delete(f"/api/users/{admin_user.pk}/")
        assert resp.status_code == 400
        assert "your own account" in resp.json()["error"]
        assert User.objects.filter(pk=admin_user.pk).exists()

    def test_can_delete_non_admin(self, admin_client, engineer_user):
        resp = admin_client.delete(f"/api/users/{engineer_user.pk}/")
        assert resp.status_code == 204
        assert not User.objects.filter(pk=engineer_user.pk).exists()

    def test_can_delete_other_admin_when_multiple(self, admin_client, admin_user):
        other = User.objects.create_user(username="admin2", password="x", role="admin")
        resp = admin_client.delete(f"/api/users/{other.pk}/")
        assert resp.status_code == 204
        assert not User.objects.filter(pk=other.pk).exists()

    def test_is_last_admin_helper(self, admin_user, engineer_user):
        """The only active admin is the last admin; non-admins never are."""
        from apps.core.views import UserViewSet
        assert UserViewSet._is_last_admin(admin_user) is True
        assert UserViewSet._is_last_admin(engineer_user) is False
        User.objects.create_user(username="admin2", password="x", role="admin")
        assert UserViewSet._is_last_admin(admin_user) is False


class TestDemoteGuards:
    def test_cannot_demote_last_admin(self, admin_client, admin_user):
        resp = admin_client.patch(
            f"/api/users/{admin_user.pk}/", {"role": "viewer"}, format="json")
        assert resp.status_code == 400
        admin_user.refresh_from_db()
        assert admin_user.role == "admin"

    def test_cannot_deactivate_last_admin(self, admin_client, admin_user):
        resp = admin_client.patch(
            f"/api/users/{admin_user.pk}/", {"is_active": False}, format="json")
        assert resp.status_code == 400
        admin_user.refresh_from_db()
        assert admin_user.is_active is True

    def test_can_demote_admin_when_another_exists(self, admin_client, admin_user, roles):
        User.objects.create_user(username="admin2", password="x", role="admin")
        resp = admin_client.patch(
            f"/api/users/{admin_user.pk}/", {"role": "engineer"}, format="json")
        assert resp.status_code == 200
        admin_user.refresh_from_db()
        assert admin_user.role == "engineer"
