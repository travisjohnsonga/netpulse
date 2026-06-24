"""TOTP MFA: enrollment, login second factor, forced privileged enrollment,
token-scope isolation, admin reset, break-glass, and the SSO split."""
import pyotp
import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.core.models import AuditLog, MFADevice

pytestmark = pytest.mark.django_db

PW = "testpass123"


@pytest.fixture(autouse=True)
def _require_privileged_mfa(settings):
    """This module verifies the production default: MFA is required for holders of
    user:manage / rbac:manage. (conftest disables it for the rest of the suite.)"""
    settings.MFA_REQUIRED_FOR_CAPABILITIES = ["user:manage", "rbac:manage"]


# ── helpers ──────────────────────────────────────────────────────────────────
def _make_user(django_user_model, username, role="viewer", usable_password=True):
    u = django_user_model.objects.create_user(username=username, password=PW, role=role)
    if not usable_password:
        u.set_unusable_password()
        u.save()
    return u


def _bearer(user):
    from rest_framework_simplejwt.tokens import RefreshToken
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return c


def _enroll(user):
    """Run setup→confirm for an already-authenticated user; return recovery codes."""
    c = _bearer(user)
    r = c.post("/api/auth/mfa/setup/", {}, format="json")
    assert r.status_code == 200, r.data
    secret = r.data["secret"]
    code = pyotp.TOTP(secret).now()
    r2 = c.post("/api/auth/mfa/confirm/", {"code": code}, format="json")
    assert r2.status_code == 200, r2.data
    return secret, r2.data["recovery_codes"]


def _current_code(secret):
    return pyotp.TOTP(secret).now()


# ── enrollment ───────────────────────────────────────────────────────────────
def test_setup_then_confirm_activates_mfa(django_user_model):
    user = _make_user(django_user_model, "alice")
    c = _bearer(user)
    r = c.post("/api/auth/mfa/setup/", {}, format="json")
    assert r.status_code == 200
    assert r.data["otpauth_uri"].startswith("otpauth://totp/")
    assert r.data["qr_code"].startswith("data:image/svg+xml;base64,")
    secret = r.data["secret"]
    # pending, not yet active
    assert MFADevice.objects.get(user=user).mfa_enabled is False

    r2 = c.post("/api/auth/mfa/confirm/", {"code": _current_code(secret)}, format="json")
    assert r2.status_code == 200
    assert r2.data["mfa_enabled"] is True
    assert len(r2.data["recovery_codes"]) == 10
    dev = MFADevice.objects.get(user=user)
    assert dev.mfa_enabled and dev.confirmed_at is not None
    assert AuditLog.objects.filter(event_type="mfa_enabled", user=user).exists()


def test_confirm_wrong_code_does_not_activate(django_user_model):
    user = _make_user(django_user_model, "bob")
    c = _bearer(user)
    c.post("/api/auth/mfa/setup/", {}, format="json")
    r = c.post("/api/auth/mfa/confirm/", {"code": "000000"}, format="json")
    assert r.status_code == 400
    assert MFADevice.objects.get(user=user).mfa_enabled is False
    assert AuditLog.objects.filter(event_type="mfa_failed", user=user).exists()


# ── login second factor ──────────────────────────────────────────────────────
def test_login_with_mfa_returns_challenge_not_token(django_user_model):
    user = _make_user(django_user_model, "carol")
    secret, _ = _enroll(user)
    r = APIClient().post("/api/auth/token/", {"username": "carol", "password": PW}, format="json")
    assert r.status_code == 200
    assert r.data.get("mfa_required") is True
    assert "challenge_token" in r.data
    assert "access" not in r.data and "refresh" not in r.data


def test_correct_code_redeems_real_jwt(django_user_model):
    user = _make_user(django_user_model, "dave")
    secret, _ = _enroll(user)
    ch = APIClient().post("/api/auth/token/", {"username": "dave", "password": PW}, format="json").data["challenge_token"]
    r = APIClient().post("/api/auth/token/mfa/",
                         {"challenge_token": ch, "code": _current_code(secret)}, format="json")
    assert r.status_code == 200
    assert "access" in r.data and "refresh" in r.data
    assert AuditLog.objects.filter(event_type="login_success", user=user).exists()


def test_wrong_code_is_forbidden_audited_and_throttle_configured(django_user_model):
    from apps.core.mfa_views import MFATokenView
    user = _make_user(django_user_model, "erin")
    _enroll(user)
    ch = APIClient().post("/api/auth/token/", {"username": "erin", "password": PW}, format="json").data["challenge_token"]
    r = APIClient().post("/api/auth/token/mfa/", {"challenge_token": ch, "code": "000000"}, format="json")
    assert r.status_code == 403
    assert AuditLog.objects.filter(event_type="mfa_failed", user=user).exists()
    assert AuditLog.objects.filter(event_type="login_failed", user=user).exists()
    # second-factor endpoint shares the brute-force "auth" throttle scope
    assert MFATokenView.throttle_scope == "auth"


def test_recovery_code_works_once_then_consumed(django_user_model):
    user = _make_user(django_user_model, "frank")
    secret, recovery = _enroll(user)
    code = recovery[0]
    ch = APIClient().post("/api/auth/token/", {"username": "frank", "password": PW}, format="json").data["challenge_token"]
    r = APIClient().post("/api/auth/token/mfa/", {"challenge_token": ch, "recovery_code": code}, format="json")
    assert r.status_code == 200 and "access" in r.data
    # reuse the same recovery code → rejected
    ch2 = APIClient().post("/api/auth/token/", {"username": "frank", "password": PW}, format="json").data["challenge_token"]
    r2 = APIClient().post("/api/auth/token/mfa/", {"challenge_token": ch2, "recovery_code": code}, format="json")
    assert r2.status_code == 403
    assert MFADevice.objects.get(user=user).recovery_codes_remaining == 9


# ── token-scope isolation (no bypass) ─────────────────────────────────────────
def test_challenge_token_is_not_an_access_token(django_user_model):
    user = _make_user(django_user_model, "grace")
    _enroll(user)
    ch = APIClient().post("/api/auth/token/", {"username": "grace", "password": PW}, format="json").data["challenge_token"]
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {ch}")
    assert c.get("/api/auth/mfa/").status_code == 401  # not a JWT → rejected


def test_neither_intermediate_token_can_refresh_into_jwt(django_user_model):
    # enrollment token (privileged user, no MFA yet)
    _make_user(django_user_model, "heidi", role="admin")
    enr = APIClient().post("/api/auth/token/", {"username": "heidi", "password": PW},
                           format="json").data["enrollment_token"]
    # challenge token (MFA-enabled user)
    henry = _make_user(django_user_model, "henry")
    _enroll(henry)
    ch = APIClient().post("/api/auth/token/", {"username": "henry", "password": PW},
                          format="json").data["challenge_token"]
    # NEITHER blob is a refresh token — /token/refresh/ rejects both, no JWT issued.
    for tok in (enr, ch):
        r = APIClient().post("/api/auth/token/refresh/", {"refresh": tok}, format="json")
        assert r.status_code == 401
        assert "access" not in r.data and "refresh" not in r.data


# ── forced enrollment for privileged accounts (A.8.2) ─────────────────────────
def test_privileged_local_user_without_mfa_is_forced_to_enroll(django_user_model):
    user = _make_user(django_user_model, "ivan", role="admin")  # holds user:manage
    r = APIClient().post("/api/auth/token/", {"username": "ivan", "password": PW}, format="json")
    assert r.status_code == 200
    assert r.data.get("mfa_enrollment_required") is True
    assert "enrollment_token" in r.data
    assert "access" not in r.data  # not a full token, but not locked out either
    assert AuditLog.objects.filter(event_type="mfa_enrollment_forced", user=user).exists()


def test_enrollment_token_reaches_only_setup_confirm(django_user_model):
    user = _make_user(django_user_model, "judy", role="admin")
    enr = APIClient().post("/api/auth/token/", {"username": "judy", "password": PW}, format="json").data["enrollment_token"]
    # works on setup (via header), forbidden elsewhere
    c = APIClient()
    r_setup = c.post("/api/auth/mfa/setup/", {}, format="json", HTTP_X_MFA_ENROLLMENT_TOKEN=enr)
    assert r_setup.status_code == 200
    # As a Bearer on a normal endpoint it is DENIED — the signing blob is not a
    # JWT, so JWTAuthentication fails outright (401) and it never reaches a
    # capability check. Verify across several normal endpoints, with no data leak.
    c2 = APIClient()
    c2.credentials(HTTP_AUTHORIZATION=f"Bearer {enr}")
    for path in ("/api/users/", "/api/auth/mfa/", "/api/devices/"):
        resp = c2.get(path)
        assert resp.status_code in (401, 403), (path, resp.status_code)
        assert "access" not in (resp.data or {})
    # And the enrollment token in the body of a normal endpoint is ignored too.
    assert APIClient().get("/api/users/", {"enrollment_token": enr}).status_code in (401, 403)


def test_forced_enrollment_completes_and_issues_jwt(django_user_model):
    user = _make_user(django_user_model, "ken", role="admin")
    enr = APIClient().post("/api/auth/token/", {"username": "ken", "password": PW}, format="json").data["enrollment_token"]
    setup = APIClient().post("/api/auth/mfa/setup/", {}, format="json",
                             HTTP_X_MFA_ENROLLMENT_TOKEN=enr)
    secret = setup.data["secret"]
    confirm = APIClient().post("/api/auth/mfa/confirm/",
                               {"code": _current_code(secret), "enrollment_token": enr}, format="json")
    assert confirm.status_code == 200
    assert "tokens" in confirm.data and "access" in confirm.data["tokens"]
    assert MFADevice.objects.get(user=user).mfa_enabled is True
    assert AuditLog.objects.filter(event_type="mfa_enrollment_completed", user=user).exists()


def test_non_privileged_local_user_is_not_forced(django_user_model):
    _make_user(django_user_model, "leo", role="viewer")  # no user:manage / rbac:manage
    r = APIClient().post("/api/auth/token/", {"username": "leo", "password": PW}, format="json")
    assert r.status_code == 200
    assert "access" in r.data and "refresh" in r.data
    assert "mfa_enrollment_required" not in r.data


# ── SSO split — no double MFA ─────────────────────────────────────────────────
def test_sso_mint_path_is_not_subject_to_local_totp(django_user_model):
    from apps.sso.views import get_tokens_for_user
    # privileged SSO-only account (no usable local password) → provider MFA covers it
    user = _make_user(django_user_model, "mallory", role="admin", usable_password=False)
    tokens = get_tokens_for_user(user)
    assert "access" in tokens and "refresh" in tokens  # full token, no challenge/enrollment


# ── admin reset + break-glass ─────────────────────────────────────────────────
def test_admin_reset_clears_mfa_and_audits(django_user_model):
    admin = _make_user(django_user_model, "nora", role="admin")
    victim = _make_user(django_user_model, "olivia", role="viewer")
    _enroll(victim)
    assert MFADevice.objects.get(user=victim).mfa_enabled is True
    r = _bearer(admin).post(f"/api/users/{victim.pk}/reset-mfa/", {}, format="json")
    assert r.status_code == 200 and r.data["had_mfa"] is True
    assert MFADevice.objects.get(user=victim).mfa_enabled is False
    log = AuditLog.objects.filter(event_type="mfa_reset_by_admin", target_id=str(victim.pk)).first()
    assert log is not None and log.user_id == admin.pk


def test_non_admin_cannot_reset_mfa(django_user_model):
    viewer = _make_user(django_user_model, "peggy", role="viewer")
    victim = _make_user(django_user_model, "quinn", role="viewer")
    r = _bearer(viewer).post(f"/api/users/{victim.pk}/reset-mfa/", {}, format="json")
    assert r.status_code == 403


def test_break_glass_command_resets_and_audits(django_user_model):
    user = _make_user(django_user_model, "rita", role="admin")
    _enroll(user)
    assert MFADevice.objects.get(user=user).mfa_enabled is True
    call_command("reset_mfa", "rita")
    assert MFADevice.objects.get(user=user).mfa_enabled is False
    log = AuditLog.objects.filter(event_type="mfa_reset_by_admin", target_id=str(user.pk)).first()
    assert log is not None and log.metadata.get("via") == "management_command"


def test_admin_reset_retriggers_forced_enrollment(django_user_model):
    """After reset, a privileged user is forced through enrollment again (no free pass)."""
    user = _make_user(django_user_model, "sam", role="admin")
    secret, _ = _enroll(user)
    # logs in with second factor fine before reset
    user.mfa_device.clear()  # simulate admin/break-glass reset
    user.mfa_device.save()
    r = APIClient().post("/api/auth/token/", {"username": "sam", "password": PW}, format="json")
    assert r.data.get("mfa_enrollment_required") is True


# ── disable ──────────────────────────────────────────────────────────────────
def test_disable_requires_valid_code(django_user_model):
    user = _make_user(django_user_model, "tara")
    secret, _ = _enroll(user)
    c = _bearer(user)
    assert c.post("/api/auth/mfa/disable/", {"code": "000000"}, format="json").status_code == 400
    assert MFADevice.objects.get(user=user).mfa_enabled is True
    r = c.post("/api/auth/mfa/disable/", {"code": _current_code(secret)}, format="json")
    assert r.status_code == 200 and r.data["mfa_enabled"] is False
    assert MFADevice.objects.get(user=user).mfa_enabled is False
    assert AuditLog.objects.filter(event_type="mfa_disabled", user=user).exists()
