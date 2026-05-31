import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

AUTH_USER_MODEL = "core.NetPulseUser"

INSTALLED_APPS = [
    # daphne must precede staticfiles to serve ASGI via `manage.py runserver`
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "channels",
    "django_celery_beat",
    # NetPulse
    "apps.core",
    "apps.devices",
    "apps.credentials",
    "apps.telemetry",
    "apps.compliance",
    "apps.alerts",
    "apps.cve",
    "apps.lifecycle",
    "apps.security",
    "apps.collectors",
    "apps.integrations",
    "apps.configbackup",
    "apps.logs",
    "apps.tls",
    "apps.checks",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ── Database ──────────────────────────────────────────────────────────────────

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 10},
    }
}

# ── Cache & Channel Layer (Valkey / Redis-compatible) ─────────────────────────

from urllib.parse import quote as _urlquote

# URL-encode the password: special chars (#, /, @, !, …) in a raw redis:// URL
# otherwise corrupt parsing — e.g. a "#" is read as a URL fragment, which made
# kombu/Celery read the port as the password text. safe="" encodes everything.
_valkey = (
    f"redis://:{_urlquote(os.environ['VALKEY_PASSWORD'], safe='')}"
    f"@{os.environ.get('VALKEY_HOST', 'valkey')}"
    f":{os.environ.get('VALKEY_PORT', '6379')}"
)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"{_valkey}/0",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [f"{_valkey}/1"]},
    }
}

# ── Celery ────────────────────────────────────────────────────────────────────

CELERY_BROKER_URL = f"{_valkey}/2"
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"

# ── InfluxDB ──────────────────────────────────────────────────────────────────

INFLUXDB_URL = os.environ.get("INFLUXDB_URL", "http://influxdb:8086")
INFLUXDB_TOKEN = os.environ.get("INFLUXDB_ADMIN_TOKEN", "")
INFLUXDB_ORG = os.environ.get("INFLUXDB_ORG", "netpulse")
INFLUXDB_BUCKET = os.environ.get("INFLUXDB_BUCKET", "metrics")

# ── OpenSearch ────────────────────────────────────────────────────────────────

OPENSEARCH_HOST = os.environ.get("OPENSEARCH_HOST", "opensearch")
OPENSEARCH_PORT = int(os.environ.get("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.environ.get("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.environ.get("OPENSEARCH_PASSWORD", "")
OPENSEARCH_USE_SSL = os.environ.get("OPENSEARCH_USE_SSL", "false").lower() == "true"

# ── NATS ──────────────────────────────────────────────────────────────────────

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
NATS_USER = os.environ.get("NATS_USER", "")
NATS_PASSWORD = os.environ.get("NATS_PASSWORD", "")

# ── OpenBao ───────────────────────────────────────────────────────────────────

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
OPENBAO_TOKEN = os.environ.get("OPENBAO_TOKEN", "")

# ── TLS ───────────────────────────────────────────────────────────────────────
# Directory holding NetPulse's OWN HTTPS server cert/key (not device certs).
# Shared with the nginx container via the ssl-certs volume. The private key
# lives here on disk (mode 0600) and is never returned by the API.
SSL_DIR = os.environ.get("SSL_DIR", str(BASE_DIR / "ssl"))

# Trusted CA bundle (system roots + admin-added CAs), rebuilt by apps.tls.
# Point outbound HTTPS (requests: CVE feeds, vendor APIs, git sync) at it when
# present so private/internal PKIs and SSL-inspection proxies are trusted.
_CA_BUNDLE = os.path.join(SSL_DIR, "ca-bundle.crt")
if os.path.exists(_CA_BUNDLE):
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _CA_BUNDLE)
    os.environ.setdefault("SSL_CERT_FILE", _CA_BUNDLE)

# IP/host of the NetPulse collector that devices send telemetry to (used when
# generating device telemetry config). Configured under Settings → General.
COLLECTOR_IP = os.environ.get("COLLECTOR_IP", "")

# Path to the web-UI TLS certificate, read by /api/health/ to report
# ssl_cert_days_remaining. Defaults to the shared ssl-certs volume the tls app
# writes and nginx serves; override with SSL_CERT_PATH if mounted elsewhere.
SSL_CERT_PATH = os.environ.get(
    "SSL_CERT_PATH",
    os.path.join(os.environ.get("SSL_CERT_DIR", "/etc/ssl/netpulse"), "netpulse.crt"),
)

# ── Config push safety ────────────────────────────────────────────────────────
# Master switch for pushing configuration to network devices. Default false
# (read-only / monitoring only); device-push endpoints return 403 unless this
# is explicitly enabled after network-team review. Exposed to the frontend via
# GET /api/settings/system/ so the UI can disable "Push to Device".
ALLOW_CONFIG_PUSH = os.environ.get("ALLOW_CONFIG_PUSH", "false").lower() == "true"

# Publish device SNMP config to NATS (netpulse.devices.upsert) on save so the
# ingest-snmp poller learns about devices. Disabled in tests (no NATS).
SNMP_DEVICE_PUBLISH = os.environ.get("SNMP_DEVICE_PUBLISH", "true").lower() == "true"

# Directory of community-maintained vendor advisory YAML (Juniper/Arista/…),
# loaded by `load_community_advisories`. Mounted from the repo's advisories/.
COMMUNITY_ADVISORIES_DIR = os.environ.get("COMMUNITY_ADVISORIES_DIR", "/app/advisories")

# ── Django REST Framework ─────────────────────────────────────────────────────

# ── JWT ───────────────────────────────────────────────────────────────────────

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME":  timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS":  False,
    "ALGORITHM":              "HS256",
    "AUTH_HEADER_TYPES":      ("Bearer",),
    # Include role + username in every access token
    "TOKEN_OBTAIN_SERIALIZER": "apps.core.serializers.NetPulseTokenObtainPairSerializer",
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "apps.core.permissions.NetPulsePermission",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "NetPulse API",
    "DESCRIPTION": "Push-first network intelligence platform API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Schema/docs are not public — the SPA fetches them with a JWT attached.
    "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"],
    "SERVE_AUTHENTICATION": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Several models expose a `status` field with different choice sets; name each
    # enum explicitly (by reference to the TextChoices class) so the generated
    # schema has no naming collisions.
    "ENUM_NAME_OVERRIDES": {
        "DeviceStatusEnum": "apps.devices.models.Device.Status",
        "DiscoveryJobStatusEnum": "apps.devices.models.DiscoveryJob.Status",
        "DiscoveredDeviceStatusEnum": "apps.devices.models.DiscoveredDevice.Status",
        "CollectorStatusEnum": "apps.collectors.models.Collector.Status",
        "InterfaceAlertSeverityEnum": "apps.telemetry.models.MonitoredInterface.AlertSeverity",
    },
}

# ── Auth ──────────────────────────────────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Localisation ──────────────────────────────────────────────────────────────

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ── Static ────────────────────────────────────────────────────────────────────

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

WHITENOISE_USE_FINDERS = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
