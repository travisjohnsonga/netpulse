import pytest

pytestmark = pytest.mark.django_db


class TestMe:
    def test_get_includes_preferences(self, auth_client, user):
        b = auth_client.get("/api/users/me/").json()
        assert b["username"] == user.username
        assert b["role"] == user.role
        assert "preferences" in b
        assert b["preferences"]["theme"] == "system"
        assert b["preferences"]["log_default_time_range"] == "1h"

    def test_update_account_fields(self, auth_client):
        resp = auth_client.put("/api/users/me/", {
            "email": "new@example.com", "first_name": "Ada", "last_name": "L",
        }, format="json")
        assert resp.status_code == 200
        b = resp.json()
        assert b["email"] == "new@example.com" and b["first_name"] == "Ada"

    def test_role_is_read_only(self, auth_client, user):
        original = user.role
        auth_client.put("/api/users/me/", {"role": "viewer", "email": "x@y.z"}, format="json")
        user.refresh_from_db()
        assert user.role == original  # role cannot be changed via this endpoint

    def test_unauthenticated(self, api_client):
        assert api_client.get("/api/users/me/").status_code == 401


class TestPreferences:
    def test_get_auto_creates(self, auth_client):
        b = auth_client.get("/api/users/me/preferences/").json()
        assert b["theme"] == "system" and b["devices_page_size"] == 25
        assert b["log_default_page_size"] == 50

    def test_update(self, auth_client):
        resp = auth_client.put("/api/users/me/preferences/", {
            "theme": "dark", "log_default_time_range": "24h",
            "log_auto_refresh": True, "devices_default_columns": ["hostname", "status"],
            "timezone": "America/Chicago", "date_format": "us",
        }, format="json")
        assert resp.status_code == 200
        b = resp.json()
        assert b["theme"] == "dark" and b["log_default_time_range"] == "24h"
        assert b["log_auto_refresh"] is True
        assert b["devices_default_columns"] == ["hostname", "status"]
        assert b["timezone"] == "America/Chicago" and b["date_format"] == "us"

    def test_invalid_theme_rejected(self, auth_client):
        assert auth_client.put("/api/users/me/preferences/", {"theme": "neon"}, format="json").status_code == 400


class TestChangePassword:
    def test_success(self, auth_client, user):
        user.set_password("oldpass123"); user.save()
        resp = auth_client.post("/api/users/me/change-password/", {
            "current_password": "oldpass123", "new_password": "BrandNew!pass99",
        }, format="json")
        assert resp.status_code == 200
        # Fresh tokens are returned so the SPA can drop the forced-change gate.
        assert "access" in resp.json() and "refresh" in resp.json()
        user.refresh_from_db()
        assert user.check_password("BrandNew!pass99")

    def test_wrong_current_rejected(self, auth_client, user):
        user.set_password("oldpass123"); user.save()
        resp = auth_client.post("/api/users/me/change-password/", {
            "current_password": "wrong", "new_password": "BrandNew!pass99",
        }, format="json")
        assert resp.status_code == 400

    def test_weak_new_rejected(self, auth_client, user):
        user.set_password("oldpass123"); user.save()
        resp = auth_client.post("/api/users/me/change-password/", {
            "current_password": "oldpass123", "new_password": "123",
        }, format="json")
        assert resp.status_code == 400

    def test_change_clears_must_change_password(self, auth_client, user):
        user.set_password("oldpass123"); user.must_change_password = True; user.save()
        resp = auth_client.post("/api/users/me/change-password/", {
            "current_password": "oldpass123", "new_password": "BrandNew!pass99",
        }, format="json")
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.must_change_password is False

    def test_new_same_as_current_rejected(self, auth_client, user):
        user.set_password("SamePass123"); user.save()
        resp = auth_client.post("/api/users/me/change-password/", {
            "current_password": "SamePass123", "new_password": "SamePass123",
        }, format="json")
        assert resp.status_code == 400

    def test_default_password_rejected(self, auth_client, user):
        user.set_password("oldpass123"); user.save()
        resp = auth_client.post("/api/users/me/change-password/", {
            "current_password": "oldpass123", "new_password": "NetPulse1!",
        }, format="json")
        assert resp.status_code == 400

    def test_missing_uppercase_or_digit_rejected(self, auth_client, user):
        user.set_password("oldpass123"); user.save()
        # no uppercase
        assert auth_client.post("/api/users/me/change-password/", {
            "current_password": "oldpass123", "new_password": "lowercase99",
        }, format="json").status_code == 400
        # no digit
        assert auth_client.post("/api/users/me/change-password/", {
            "current_password": "oldpass123", "new_password": "NoDigitsHere",
        }, format="json").status_code == 400


class TestMustChangePasswordFlag:
    def test_login_response_includes_flag(self, api_client, user):
        user.set_password("loginpass123"); user.must_change_password = True; user.save()
        resp = api_client.post("/api/auth/token/", {
            "username": user.username, "password": "loginpass123",
        }, format="json")
        assert resp.status_code == 200
        assert resp.json().get("must_change_password") is True

    def test_me_exposes_flag(self, auth_client, user):
        user.must_change_password = True; user.save()
        resp = auth_client.get("/api/users/me/")
        assert resp.status_code == 200
        assert resp.json().get("must_change_password") is True
