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

## Known gaps

- No MFA, no password-expiry policy, and no refresh-token revocation/blacklist
  are implemented.
- SAML and LDAP appear as `SSOProvider` choices but have no functional backend
  yet (only Google/Azure/Okta/GitHub OAuth2 are wired).
