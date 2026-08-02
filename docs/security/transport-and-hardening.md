# Transport & Hardening

External-facing traffic is TLS-only in production, and Django ships a set of
HTTP security headers and cookie flags. **These settings live in
`config/settings/production.py` and are production-gated** — they apply only when
`DJANGO_SETTINGS_MODULE=config.settings.production` (set in the api image's
`Dockerfile`). The base/test settings do not enable them, so a dev or test run is
not representative of the production posture.

`production.py` does `from .base import *` and then layers the hardening below.

## TLS / HSTS

`config/settings/production.py`:

```python
SECURE_HSTS_SECONDS = 31536000          # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
```

`SECURE_SSL_REDIRECT` is environment-driven (`SECURE_SSL_REDIRECT`, defaulting to
off in code but shipped on via `.env.example`); when enabled Django redirects
plain HTTP to HTTPS. The nginx layer also redirects `:80 → :443`.

`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` tells Django to
trust the `X-Forwarded-Proto` header from the nginx proxy when deciding whether a
request arrived over HTTPS.

## Cookies

```python
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
```

Session and CSRF cookies are marked secure (HTTPS-only) with `SameSite=Lax`; the
session cookie is also `HttpOnly`.

## CSRF trusted origins

`CSRF_TRUSTED_ORIGINS` is populated from the environment (scheme-qualified,
comma-separated), defaulting to an empty list:

```python
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
```

Set this to your deployment's public origin(s), e.g.
`https://spane.example.com`.

## Content-type and clickjacking

`SECURE_CONTENT_TYPE_NOSNIFF = True` is set in `production.py`. Clickjacking
protection comes from Django's `XFrameOptionsMiddleware` (enabled in base
settings; Django's default is `DENY`). nginx additionally sets the header
explicitly for the static SPA (below).

## nginx (front door)

`services/frontend/nginx.conf` terminates TLS for the SPA and proxies the API:

```nginx
ssl_protocols       TLSv1.2 TLSv1.3;
ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...:DHE-RSA-AES256-GCM-SHA384;
ssl_prefer_server_ciphers off;
...
add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;
add_header Referrer-Policy strict-origin-when-cross-origin always;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

So the edge is **TLS 1.2 and 1.3, with the Mozilla "intermediate" cipher suites
(strong ECDHE GCM + CHACHA20) and a TLS 1.2 floor** — TLS 1.0/1.1/SSLv3 stay
disabled. TLS 1.2 is kept intentionally for client compatibility: stock Windows
Server PowerShell 5.1 (.NET Framework / Schannel) cannot negotiate TLS 1.3, so
the agent install one-liner must be reachable over 1.2. Plus
`X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, and HSTS
applied to the served frontend.

## Caching policy

A deterministic cache policy serves two ends — correct SPA delivery and keeping
secrets out of caches (a data-protection control, ISO 27001 A.8.* / A.5.14):

- **SPA shell (`index.html`) — `Cache-Control: no-cache, no-store, must-revalidate`.**
  The shell references content-hashed asset filenames, so it must never be served
  stale; this makes a `rebuild-frontend` show up immediately (no hard-refresh).
- **Hashed assets (`/assets/…`) — `Cache-Control: public, max-age=31536000, immutable`.**
  Vite emits content-hashed names, so a URL's bytes never change — cache forever;
  a rebuild changes the hash (referenced by the fresh `index.html`), so clients
  fetch new assets without re-downloading unchanged ones.
- **Secret-bearing API responses — `Cache-Control: no-store.`** Applied narrowly
  (not blanket) to responses that carry credentials/tokens so they're never
  written to browser/proxy/disk caches: the JWT obtain/refresh + MFA challenge
  (`apps/core/throttled_auth.py`), agent enrollment-token generation and the
  enroll response (the signed cert), and collector enrollment/API-key/cert
  responses (`apps/agents/views.py`, `apps/collectors/views.py`, via
  `apps/core/http.py:NoStoreResponseMixin`/`add_no_store`).

## Known gaps

These were verified absent at the time of writing — treat them as future
hardening, not implemented controls:

- `CSRF_COOKIE_HTTPONLY` is not explicitly set (Django default is `False`; the
  SPA reads the CSRF cookie, so this is expected, but it is not hardened to
  `True`).
- `SECURE_BROWSER_XSS_FILTER` and an explicit `X_FRAME_OPTIONS` value are not set
  in Django settings (clickjacking is covered by the middleware default and the
  nginx header).
- No Content-Security-Policy header is configured.
