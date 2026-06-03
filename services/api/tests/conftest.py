import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


@pytest.fixture(autouse=True)
def _never_touch_real_vault(monkeypatch):
    """Hard guarantee: no test can write to / read from a real OpenBao.

    config.settings.test already sets OPENBAO_DISABLED=True, but the api
    container mounts the openbao-data volume and can resolve the live root
    token — so a misconfigured settings module (e.g. accidentally running the
    suite under config.settings.development) would let the credential
    integration tests leak their real-looking fixture secrets into the real
    vault at netpulse/credentials/{pk}. This autouse guard forces the vault
    helper closed for every test regardless of settings. Tests that need a
    (fake, in-memory) live vault re-enable it explicitly afterwards via their
    own monkeypatch (see tests/test_vault_placeholders.py::live_vault), which
    wins because it is applied after this autouse fixture.
    """
    monkeypatch.setattr(
        "apps.credentials.vault.vault_enabled", lambda: False, raising=True
    )


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
