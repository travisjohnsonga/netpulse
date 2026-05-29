import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


def _make_user(username, role, **kwargs):
    return User.objects.create_user(
        username=username,
        password="testpass123",
        role=role,
        **kwargs,
    )


def _auth_client_for(user):
    client = APIClient()
    refresh = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return client


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    # Default test user is admin so all existing CRUD tests continue to pass.
    return _make_user("testuser", role="admin")


@pytest.fixture
def auth_client(user):
    return _auth_client_for(user)


# ── Per-role fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def admin_user(db):
    return _make_user("admin_user", role="admin")


@pytest.fixture
def engineer_user(db):
    return _make_user("engineer_user", role="engineer")


@pytest.fixture
def viewer_user(db):
    return _make_user("viewer_user", role="viewer")


@pytest.fixture
def api_user(db):
    return _make_user("api_user", role="api")


@pytest.fixture
def admin_client(admin_user):
    return _auth_client_for(admin_user)


@pytest.fixture
def engineer_client(engineer_user):
    return _auth_client_for(engineer_user)


@pytest.fixture
def viewer_client(viewer_user):
    return _auth_client_for(viewer_user)


@pytest.fixture
def api_svc_client(api_user):
    return _auth_client_for(api_user)
