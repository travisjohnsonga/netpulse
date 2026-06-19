import os

from .base import *  # noqa: F401, F403

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]
# Never start with an empty list — that rejects every request (a misconfigured
# .env would otherwise lock everyone out, including the health checks).
if not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
# Development / troubleshooting escape hatch: DJANGO_DEBUG=true allows any host.
# DEBUG is inherited from base.py. Keep DJANGO_DEBUG=false in production.
if DEBUG:  # noqa: F405
    ALLOWED_HOSTS = ["*"]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Force HTTPS at the app layer too (nginx already redirects :80→:443, but this
# also covers any path that reaches Django over plain HTTP directly). Safe behind
# the proxy via SECURE_PROXY_SSL_HEADER above. Shipped ON in .env.example; kept
# env-driven (default off) so the over-HTTP test/health harness isn't redirected.
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "false").lower() == "true"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Session/admin/SSO flows use CSRF; trust the configured external origins
# (comma-separated, scheme-qualified, e.g. https://spane.example.com).
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True

CORS_ALLOWED_ORIGINS = [
    h.strip() for h in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if h.strip()
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(name)s %(levelname)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
