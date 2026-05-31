import os

# Set required env vars before importing base so it doesn't raise KeyError.
os.environ.setdefault("DJANGO_SECRET_KEY", "insecure-test-key-not-for-production")
os.environ.setdefault("POSTGRES_DB", "netpulse_test")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "postgres")
os.environ.setdefault("VALKEY_PASSWORD", "test")

from .base import *  # noqa: F401, F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# SQLite — no external DB needed for unit tests.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# No NATS in the unit-test environment — don't publish device configs on save.
SNMP_DEVICE_PUBLISH = False

# Disable the auth throttle by default so the suite is deterministic; the
# throttle test re-enables a tiny rate via override_settings.
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"auth": None}}  # noqa: F405

LOGGING = {"version": 1, "disable_existing_loggers": True}
