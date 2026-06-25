# Multi-Factor Authentication (TOTP)

This is the operator/admin guide to spane's multi-factor authentication: how
users enrol, how it's required for privileged accounts, and — critically — how to
recover a locked-out account. For the implementation/security details (how the
secret is stored, the intermediate-token design, the audit events), see
[Authentication](authentication.md); this page does not duplicate them.

## What it is

spane supports **time-based one-time-password** MFA (RFC 6238). It works with any
standard authenticator app — **Google Authenticator, Microsoft Authenticator,
Authy, 1Password**, and others.

**Local password accounts only.** TOTP applies to users who sign in with a
username and password. **SSO accounts (Google/Azure/Okta/GitHub) authenticate —
and MFA — at their identity provider**, so spane does not add app-level TOTP on
top (no double-MFA). A user signing in with a password is on the local path and
subject to TOTP policy; an SSO sign-in is covered by the provider's MFA.

## Enrolling (any user)

In **Profile → Two-Factor Authentication**:

1. Choose **Enable two-factor authentication**.
2. Scan the QR code with your authenticator app (or type the shown key in by
   hand).
3. Enter the 6-digit code the app displays to confirm.
4. **Save your recovery codes** — they're shown **once**. Each one signs you in a
   single time if you lose your authenticator. Copy or download them and store
   them somewhere safe (not in the browser).

Afterwards the section shows MFA is **On** and how many recovery codes remain.
**Turn off two-factor** requires a current code (or a recovery code).

At the next sign-in, after the password step you'll be asked for a 6-digit code;
there's a **"use a recovery code"** option if you don't have your app.

## Required MFA for privileged accounts

MFA is **required** for holders of the capabilities in
`MFA_REQUIRED_FOR_CAPABILITIES` (default **`user:manage`, `rbac:manage`** — the
privileged roles; ISO A.8.2). You can also require it for **all** local accounts
via the org-wide `mfa_required_all_local` system setting (or the
`MFA_REQUIRED_FOR_ALL_LOCAL` default). Setting `MFA_REQUIRED_FOR_CAPABILITIES`
empty disables forced enrollment (per-user opt-in still works).

**Forced enrollment** is safe by design — it never locks anyone out and is never
a silent bypass. When a required local user without MFA signs in with a correct
password, they are **not** given a full session. Instead they're routed straight
into setup with a restricted, single-purpose token that authorizes **only** the
MFA setup/confirm steps — they can't reach the rest of the app. Once they finish
setup → confirm, the real session is issued. (The restricted token can't be used
as an access token or refreshed into one.)

## Admin operations

### Reset a user's MFA (lost device)

An admin with **`user:manage`** can clear a user's MFA in **Settings → Users →
Reset 2FA** (per-user action, with a confirm dialog). This lets the user re-enrol.
It **never reveals the secret**, and the action is **audit-logged**. Because the
reset clears MFA, a *privileged* user is re-forced through enrollment on their
next sign-in — it is not a free pass.

### Break-glass: recover a locked-out superadmin

!!! warning "If a privileged user loses their authenticator and no other admin can reset it"
    The immutable superadmin is always MFA-required, so a lost device could
    otherwise brick admin access. The recovery path is a **console command** run
    on the server (host/stack access required):

    ```bash
    docker compose exec api python manage.py reset_mfa <username>
    ```

    It clears that user's MFA so they can re-enrol on next sign-in, and the action
    is audit-logged. This guarantees a lost privileged device can never
    permanently lock you out of administration.

## Secret & recovery-code handling

At a high level (full detail in [Authentication](authentication.md)): the TOTP
secret is treated as a credential — stored in **OpenBao** when configured, else
**encrypted** at rest — and is **never returned by the API once active or
logged**. Recovery codes are stored **hashed** and are **single-use**.

## Audited events

Every MFA action writes an `AuditLog` event (the secret and codes are never
logged): `mfa_enabled`, `mfa_disabled`, `mfa_failed`, `mfa_reset_by_admin`,
`mfa_enrollment_forced`, `mfa_enrollment_completed`. View them under
**Settings → Audit Log**.

## Settings reference

| Setting | Default | Purpose |
|---------|---------|---------|
| `MFA_REQUIRED_FOR_CAPABILITIES` | `user:manage,rbac:manage` | Capabilities whose local holders must use MFA (empty = no forced enrollment). |
| `MFA_REQUIRED_FOR_ALL_LOCAL` | `false` | Require MFA for all local accounts (the `mfa_required_all_local` system setting overrides this). |
| `MFA_ISSUER` | `spane` | Issuer label shown in the authenticator app. |
| `MFA_INTERMEDIATE_TOKEN_TTL_S` | `300` | Lifetime (s) of the login-challenge / forced-enrollment tokens. |
| `MFA_RECOVERY_CODE_COUNT` | `10` | Recovery codes generated at enrollment. |

The TOTP verification window is fixed at ±1 step (~±30 s) in code; it is not a
setting.
