"""TOTP (RFC 6238) multi-factor-auth helpers.

Covers secret-at-rest crypto, time-step code verification (with skew + replay
window), single-use recovery codes, the short-lived single-purpose intermediate
tokens used by the login second-factor and forced-enrollment flows, and the
policy that decides whether a given login must present a second factor.

Security properties (see docs/security/authentication.md):

* The TOTP secret is a credential — encrypted at rest and never returned by the
  API or logged. In OpenBao-configured deployments it lives in OpenBao
  (``netpulse/mfa/{user_id}``); otherwise a Fernet-encrypted DB column holds it.
  Never plaintext in the DB. (See ``MFADevice.set_secret``/``get_secret``.)
* Recovery codes are stored hashed (PBKDF2, same as passwords) and are single-use.
* The intermediate tokens are ``django.core.signing`` blobs, **not** JWTs, so
  DRF's ``JWTAuthentication`` can never accept them as access tokens. Distinct
  salts give the login *challenge* and forced *enrollment* tokens separate,
  non-interchangeable scopes, each expiring in ``MFA_INTERMEDIATE_TOKEN_TTL_S``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core import signing

# RFC 6238 parameters (defaults match every standard authenticator app).
TOTP_INTERVAL = 30
TOTP_DIGITS = 6
# Accept the adjacent steps to tolerate clock skew / typing latency (±1 = ~±30s).
VALID_WINDOW = 1

# Distinct salts → a challenge token cannot be loaded as an enrollment token (and
# vice versa); the embedded ``purpose`` is a second, explicit guard.
_CHALLENGE_SALT = "netpulse.mfa.challenge.v1"
_ENROLLMENT_SALT = "netpulse.mfa.enrollment.v1"


def _ttl() -> int:
    return int(getattr(settings, "MFA_INTERMEDIATE_TOKEN_TTL_S", 300))


# ── Secret encryption (at-rest fallback when OpenBao is unconfigured) ────────
def _fernet():
    from cryptography.fernet import Fernet

    # Derive a stable Fernet key from the Django secret key (which itself is an
    # env/OpenBao-managed secret in production — never hard-coded).
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


# ── TOTP ────────────────────────────────────────────────────────────────────
def generate_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def provisioning_uri(secret: str, account_name: str) -> str:
    """The ``otpauth://`` URI an authenticator app scans/imports."""
    import pyotp

    issuer = getattr(settings, "MFA_ISSUER", "spane")
    return pyotp.TOTP(secret, interval=TOTP_INTERVAL, digits=TOTP_DIGITS).provisioning_uri(
        name=account_name, issuer_name=issuer,
    )


def qr_data_uri(provisioning_uri_str: str) -> str:
    """An SVG QR of the provisioning URI as a ``data:`` URI (no Pillow needed)."""
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(provisioning_uri_str, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def _current_step(for_time: float | None = None) -> int:
    return int((for_time if for_time is not None else time.time()) // TOTP_INTERVAL)


def matching_step(secret: str, code: str, for_time: float | None = None):
    """Return the time-step a valid ``code`` matches within the skew window, else
    ``None``. Constant-time comparison; callers use the returned step for the
    replay guard (reject a step already consumed)."""
    import pyotp

    code = (code or "").strip().replace(" ", "")
    if not code.isdigit():
        return None
    totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL, digits=TOTP_DIGITS)
    base = _current_step(for_time)
    for off in range(-VALID_WINDOW, VALID_WINDOW + 1):
        step = base + off
        if hmac.compare_digest(str(totp.generate_otp(step)), code):
            return step
    return None


# ── Recovery codes (hashed, single-use) ─────────────────────────────────────
def generate_recovery_codes(n: int | None = None) -> list[str]:
    n = n or int(getattr(settings, "MFA_RECOVERY_CODE_COUNT", 10))
    return [f"{(h := secrets.token_hex(5))[:5]}-{h[5:]}" for _ in range(n)]


def _normalize_recovery(code: str) -> str:
    return (code or "").strip().lower().replace("-", "").replace(" ", "")


def hash_recovery_code(code: str) -> str:
    return make_password(_normalize_recovery(code))


def verify_recovery_code(code: str, hashed: str) -> bool:
    if not code or not hashed:
        return False
    return check_password(_normalize_recovery(code), hashed)


# ── Intermediate tokens (django.core.signing — NOT JWTs) ─────────────────────
def make_challenge_token(user) -> str:
    """A login *second-factor* token: proves password auth passed; can ONLY be
    redeemed at POST /api/auth/token/mfa/ for the real JWT pair."""
    return signing.dumps({"uid": user.pk, "purpose": "mfa-challenge"}, salt=_CHALLENGE_SALT)


def load_challenge_token(token: str) -> int:
    data = signing.loads(token, salt=_CHALLENGE_SALT, max_age=_ttl())
    if not isinstance(data, dict) or data.get("purpose") != "mfa-challenge":
        raise signing.BadSignature("not an MFA challenge token")
    return int(data["uid"])


def make_enrollment_token(user) -> str:
    """A *forced-enrollment* token: authorizes ONLY the MFA setup/confirm
    endpoints for a privileged local user who has no MFA yet. Grants no access
    scope and cannot be refreshed into a JWT."""
    return signing.dumps({"uid": user.pk, "purpose": "mfa-enrollment"}, salt=_ENROLLMENT_SALT)


def load_enrollment_token(token: str) -> int:
    data = signing.loads(token, salt=_ENROLLMENT_SALT, max_age=_ttl())
    if not isinstance(data, dict) or data.get("purpose") != "mfa-enrollment":
        raise signing.BadSignature("not an MFA enrollment token")
    return int(data["uid"])


# ── Policy ──────────────────────────────────────────────────────────────────
def mfa_required_for(user) -> bool:
    """Whether a LOCAL account MUST have MFA configured.

    True when the org-wide toggle is on (the ``mfa_required_all_local`` system
    setting, falling back to ``MFA_REQUIRED_FOR_ALL_LOCAL``), or the user holds
    any capability in ``MFA_REQUIRED_FOR_CAPABILITIES`` (privileged access —
    ISO A.8.2). Superusers hold the full capability set, so they are always
    covered (with a console break-glass reset for lost devices).
    """
    from .models import SystemSetting
    from .permissions import capabilities_of

    org = SystemSetting.get("mfa_required_all_local")
    org_required = (org == "true") if org is not None else bool(
        getattr(settings, "MFA_REQUIRED_FOR_ALL_LOCAL", False))
    if org_required:
        return True
    required = set(getattr(settings, "MFA_REQUIRED_FOR_CAPABILITIES", []))
    return bool(required & capabilities_of(user))


def evaluate_login_mfa(user) -> str:
    """For a user who just passed PASSWORD auth, decide the second-factor path:

    * ``"challenge"`` — MFA is active; require a TOTP/recovery code.
    * ``"enroll"``    — MFA is required for this (privileged/org-policy) account
      but not yet set up; force enrollment before issuing any full token.
    * ``"none"``      — issue the JWT pair directly.

    Only ever reached from the password-login endpoint, so the subject is by
    definition a local account; SSO logins mint their JWT on a different path and
    are covered by the provider's MFA.
    """
    device = getattr(user, "mfa_device", None)
    if device is not None and device.mfa_enabled:
        return "challenge"
    if mfa_required_for(user):
        return "enroll"
    return "none"
