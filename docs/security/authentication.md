# Authentication

spane authenticates API clients with JSON Web Tokens (SimpleJWT). SSO logins
mint the same token as local username/password logins, so everything downstream
(authorization, audit) behaves identically regardless of how a user signed in.

## JWT model

The token configuration lives in `config/settings/base.py` (`SIMPLE_JWT`):

| Setting | Value |
|---------|-------|
| `ACCESS_TOKEN_LIFETIME` | 1 hour |
| `REFRESH_TOKEN_LIFETIME` | 7 days |
| `ROTATE_REFRESH_TOKENS` | `False` |
| `ALGORITHM` | `HS256` (symmetric, signed with the Django secret key) |
| `AUTH_HEADER_TYPES` | `("Bearer",)` |
| `TOKEN_OBTAIN_SERIALIZER` | `apps.core.serializers.NetPulseTokenObtainPairSerializer` |

Because `ROTATE_REFRESH_TOKENS` is `False` and `BLACKLIST_AFTER_ROTATION` is not
set, refresh tokens are **not** rotated and there is no server-side token
blacklist — a refresh token remains valid until it expires (7 days). Plan token
lifetimes and revocation expectations around that.

### Token claims

`NetPulseTokenObtainPairSerializer` (`apps/core/serializers.py`) adds these
claims to the access token on top of the standard SimpleJWT claims
(`exp`, `iat`, `jti`, `user_id`, `token_type`):

- `username`
- `role` (the legacy role string)
- `email`
- `name` (display name)
- `must_change_password`

Capabilities are **not** embedded in the token. They are resolved per-request
from the user's RBAC role (see [Authorization](authorization.md)), so a role
change takes effect on the next request rather than requiring token reissue.

## Authentication & permission classes

`REST_FRAMEWORK` in `config/settings/base.py` sets:

```python
"DEFAULT_AUTHENTICATION_CLASSES": [
    "rest_framework_simplejwt.authentication.JWTAuthentication",
    "rest_framework.authentication.SessionAuthentication",
],
"DEFAULT_PERMISSION_CLASSES": [
    "apps.core.permissions.DenyByDefault",
],
```

JWT is the primary scheme; session auth supports the OAuth2 post-login flow. The
default permission class is deny-by-default — covered in
[Authorization](authorization.md).

### Login rate limiting

`DEFAULT_THROTTLE_RATES` scopes the token endpoints:

- `auth` — default `10/min` (override via `AUTH_THROTTLE_RATE`)
- `chatops` — default `30/min` (override via `CHATOPS_THROTTLE_RATE`)

Client IP is derived honoring `NUM_PROXIES` (default 1) so throttling keys on the
real client behind the nginx proxy.

## Passwords

`AUTH_PASSWORD_VALIDATORS` (`config/settings/base.py`) enables Django's four
standard validators:

- `UserAttributeSimilarityValidator`
- `MinimumLengthValidator`
- `CommonPasswordValidator`
- `NumericPasswordValidator`

The change-password path adds explicit complexity rules in
`ChangePasswordSerializer.validate_new_password` (`apps/core/serializers.py`):
at least 8 characters, at least one uppercase letter, at least one digit, and the
default admin password is rejected outright. Django's configured validators run
after these checks.

### Forced first-login change

The custom user model is `core.NetPulseUser` (`AUTH_USER_MODEL =
"core.NetPulseUser"`). It carries a `must_change_password` boolean
(`apps/core/models.py`). The seeded initial admin is created with
`must_change_password=True` (`apps/core/management/commands/ensure_superuser.py`).
The flag is surfaced both as a JWT claim and in the login response body
(`apps/core/serializers.py`), so the SPA can force a password change before
proceeding. It is cleared in `ChangePasswordSerializer.save()` once a new
password is set.

## Single sign-on (SSO)

SSO is implemented in `apps/sso` on top of `social-auth-app-django`.
`AUTHENTICATION_BACKENDS` (`config/settings/base.py`) registers dynamic OAuth2
backends for Google, Azure AD, Okta and GitHub, plus the local
`ModelBackend` fallback:

- `SSO_ALLOW_LOCAL_LOGIN` defaults to `true` — local admin login is always
  available as an IdP-outage escape hatch.
- `SSO_DEFAULT_ROLE` defaults to `viewer`.

### Provider configuration and secrets

`SSOProvider` (`apps/sso/models.py`) stores the non-secret config (`client_id`,
`tenant_id`, `okta_domain`, `allowed_domains`, `default_role`, `allow_signup`).
The OAuth **client secret is never stored in the database** — it lives in OpenBao
at `secret/sso/{id}/credentials` (key `client_secret`), referenced by
`vault_path`. The dynamic backends read `client_id` from the DB row and the
secret from OpenBao at request time (`apps/sso/backends.py`).

### Pipeline enforcement

The custom pipeline steps (`apps/sso/pipeline.py`) enforce access policy:

- `check_allowed_domain` — rejects logins whose email domain isn't in
  `provider.allowed_domains` (when set) and blocks first-time users when
  `allow_signup` is `False` (raises `AuthForbidden`).
- `assign_default_role` — new users get `provider.default_role` (default
  `viewer`).
- `sync_user_profile` — backfills name/email from the IdP without overwriting
  existing values.

### Same JWT as local login

`get_tokens_for_user` (`apps/sso/views.py`) mints the token via the same
`NetPulseTokenObtainPairSerializer` used by local login, so SSO-issued tokens
carry the identical claim set (username/role/email/name/must_change_password).

## Multi-factor authentication (TOTP)

spane supports **time-based one-time-password** MFA (RFC 6238 — Google
Authenticator, Microsoft Authenticator, Authy, 1Password, etc.) for **local
password accounts**. The implementation is a lean `pyotp` integration into the
JWT flow (`apps/core/mfa.py`, `apps/core/mfa_views.py`); it is not session- or
template-based.

> **Operator guide:** for how users enrol, how MFA is required for privileged
> accounts, admin reset, and the **break-glass recovery command**, see
> [Multi-Factor Authentication](mfa.md). This section covers the security model.

### Local accounts only — the SSO split

TOTP applies to **local password logins** (the `/api/auth/token/` path). **SSO
accounts authenticate — and MFA — at their identity provider**, so spane does
**not** layer app-level TOTP on social logins (no double-MFA). The split is
structural: SSO logins mint their JWT on a different path
(`get_tokens_for_user`) that never reaches the local MFA gate. A user who logs
in with a password is, by definition, on the local path and subject to local
TOTP policy.

### Secret & recovery-code storage

The TOTP secret is treated as a credential (`MFADevice`, `apps/core/models.py`):
in OpenBao-configured deployments it lives in OpenBao at `netpulse/mfa/{user_id}`;
otherwise it is held **Fernet-encrypted** in the DB. It is **never** stored
plaintext, returned by the API once active, or logged. Recovery codes are stored
**hashed** (PBKDF2, like passwords), shown **once** at generation, and are
**single-use**. TOTP verification allows a ±1 step skew and rejects replay of an
already-consumed step (`MFADevice.last_step`).

### Enrollment

1. `POST /api/auth/mfa/setup/` — generates a *pending* secret, returns the
   `otpauth://` provisioning URI + an SVG-data-URI QR.
2. `POST /api/auth/mfa/confirm/` `{code}` — verifies a code against the pending
   secret (proving the user scanned it), activates MFA, and returns one-time
   recovery codes.
3. `POST /api/auth/mfa/disable/` `{code}` — requires a valid TOTP/recovery code
   from the authenticated owner to turn MFA off.

### Login second factor

When a local user with MFA enabled posts a valid username/password to
`/api/auth/token/`, spane does **not** issue a JWT. It returns a short-lived
(`MFA_INTERMEDIATE_TOKEN_TTL_S`, default 5 min) single-purpose **challenge
token**. The client then posts `POST /api/auth/token/mfa/` `{challenge_token,
code|recovery_code}` to receive the real access+refresh pair. Failures are
throttled on the same `auth` scope as the password endpoint and audited.

### Required MFA for privileged accounts (A.8.2)

`MFA_REQUIRED_FOR_CAPABILITIES` (default `user:manage`, `rbac:manage`) and the
org-wide `mfa_required_all_local` toggle (or `MFA_REQUIRED_FOR_ALL_LOCAL`) make
MFA mandatory for the listed local accounts. A privileged local user **without**
MFA who logs in is **not** locked out and **not** silently let through: they
receive a restricted **enrollment token** that authorizes **only** the MFA
setup/confirm endpoints. After they complete setup→confirm, the real JWT pair is
issued. They cannot reach any other endpoint until MFA is active.

### Token-scope isolation (no bypass)

The challenge and enrollment tokens are `django.core.signing` blobs with
distinct salts/purposes — **not JWTs** — so DRF's `JWTAuthentication` rejects
them as access tokens and `/api/auth/token/refresh/` rejects them as refresh
tokens. The enrollment token is honored only by the setup/confirm views; every
other endpoint denies it.

### Admin reset & break-glass

An admin with `user:manage` can clear a user's MFA (lost-device recovery) via
`POST /api/users/{id}/reset-mfa/` — audited, and it does **not** expose the
secret. Because a reset clears `mfa_enabled`, a privileged user is re-forced
through enrollment on their next login (not a free pass).

For a locked-out account that the API can't rescue — notably the immutable
superadmin, who is always MFA-required — there is a **console break-glass**:

```bash
docker compose exec api python manage.py reset_mfa <username>
```

It clears the user's MFA from the server console (host/stack access required) and
is audit-logged. This guarantees a lost privileged device can never permanently
brick admin access.

### Audited MFA events

`mfa_enabled`, `mfa_disabled`, `mfa_failed`, `mfa_reset_by_admin`,
`mfa_enrollment_forced`, `mfa_enrollment_completed` (`AuditLog.EventType`). The
secret and recovery codes are never logged.

## Known gaps

- No password-expiry policy and no refresh-token revocation/blacklist are
  implemented. (A refresh token issued before MFA was enabled stays valid for its
  7-day life; forced privileged enrollment is unaffected, since it precedes any
  full-token issuance.)
- The enrollment **UI** (QR screen, login second-factor prompt) is a separate
  frontend follow-up; this is the backend implementation.
- SAML and LDAP appear as `SSOProvider` choices but have no functional backend
  yet (only Google/Azure/Okta/GitHub OAuth2 are wired).
