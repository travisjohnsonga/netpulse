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

# Don't spawn a real discovery scan thread when a job is created in tests.
DISCOVERY_AUTORUN = False

# Don't spawn a real enrichment probe when a device is approved in tests.
DEVICE_AUTO_ENRICH = False

# Never touch a real OpenBao from the test suite. The api container mounts the
# openbao-data volume, so the vault helper would otherwise resolve the live root
# token from /openbao/data/.init_keys and the integration tests would leak their
# fixture secrets (e.g. "sup3r-secret-pw") into the real vault at
# netpulse/credentials/{pk}. Those survive a soft factory reset and get read
# back by a newly-created profile that reuses the same pk.
OPENBAO_DISABLED = True

# Disable the auth throttle by default so the suite is deterministic; the
# throttle test re-enables a tiny rate via override_settings.
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": {"auth": None}}  # noqa: F405

LOGGING = {"version": 1, "disable_existing_loggers": True}

# Ensure STATIC_ROOT exists so WhiteNoise doesn't warn ("No directory at:
# /app/staticfiles/") when its middleware initialises during tests.
os.makedirs(STATIC_ROOT, exist_ok=True)  # noqa: F405
