import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"

# Display/brand name (was "NetPulse", now "spane"). Used for user-facing copy;
# technical identifiers (model/class names, OpenBao paths, container/image and
# service names, the GitHub repo) intentionally keep the legacy "netpulse" form.
SITE_NAME = "spane"

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
    "social_django",
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
    "apps.agents",
    "apps.collectors",
    "apps.integrations",
    "apps.configbackup",
    "apps.logs",
    "apps.flows",
    "apps.tls",
    "apps.checks",
    "apps.alerting",
    "apps.mibs",
    "apps.arp_mac",
    "apps.sso",
    "apps.frameworks",
    "apps.reports",
    "apps.backup",
    "apps.circuits",
    "apps.chatops",
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
    # Translates social-auth exceptions (AuthForbidden, etc.) into redirects.
    "social_django.middleware.SocialAuthExceptionMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# The suite is pytest-style and lives in services/api/tests/, so Django's
# default unittest DiscoverRunner finds nothing. Delegate `manage.py test`
# to pytest (see config/test_runner.py). `python -m pytest` is still canonical.
TEST_RUNNER = "config.test_runner.PytestTestRunner"

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

# SNMP MIB tree (standard / vendor / community / custom), mounted from ./mibs.
MIBS_DIR = os.environ.get("MIBS_DIR", "/app/mibs")

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

# ── Email (alert notifications) ───────────────────────────────────────────────

EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() in ("1", "true", "yes")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "spane Alerts <netpulse@localhost>")
DEFAULT_FROM_EMAIL = EMAIL_FROM
# Default to the SMTP backend; tests use the in-memory backend (pytest-django).
EMAIL_BACKEND = os.environ.get(
    "EMAIL_BACKEND",
    "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend",
)

# ── OpenBao ───────────────────────────────────────────────────────────────────

OPENBAO_ADDR = os.environ.get("OPENBAO_ADDR", "http://openbao:8200")
OPENBAO_TOKEN = os.environ.get("OPENBAO_TOKEN", "")

# ── CVE intelligence feeds (apps.cve) ─────────────────────────────────────────
# NVD/PSIRT keys default to env; the Settings → Data Sources UI can override the
# NVD key via OpenBao (CVEFeedSettings). PSIRT is optional — skipped when unset.
NVD_API_KEY = os.environ.get("NVD_API_KEY", "")
CISCO_PSIRT_CLIENT_ID = os.environ.get("CISCO_PSIRT_CLIENT_ID", "")
CISCO_PSIRT_CLIENT_SECRET = os.environ.get("CISCO_PSIRT_CLIENT_SECRET", "")
# How often the cve-engine re-syncs (hours) and the NVD page size (max 2000).
CVE_SYNC_INTERVAL_HOURS = int(os.environ.get("CVE_SYNC_INTERVAL_HOURS", "24"))
NVD_RESULTS_PER_PAGE = int(os.environ.get("NVD_RESULTS_PER_PAGE", "2000"))

# ── TLS ───────────────────────────────────────────────────────────────────────
# Directory holding NetPulse's OWN HTTPS server cert/key (not device certs).
# Shared with the nginx container via the ssl-certs volume. The private key
# lives here on disk (mode 0600) and is never returned by the API.
SSL_DIR = os.environ.get("SSL_DIR", str(BASE_DIR / "ssl"))

# NetPulse Agent assets served by the /agent/{install,download/*} endpoints:
# scripts/install.sh (the install one-liner target) and dist/<platform> binaries
# built by CI. Defaults to the in-repo `agent/` dir; in container deployments set
# AGENT_DIR to a mounted volume populated with the CI artifacts.
AGENT_DIR = os.environ.get("AGENT_DIR", str(BASE_DIR.parent.parent / "agent"))

# Agent PKI CA cert, written by setup_agent_pki onto the shared ssl-certs volume
# so the nginx (frontend) container can use it as ssl_client_certificate to
# verify agent mTLS connections. Defaults under SSL_DIR (shared with nginx).
AGENT_CA_FILE = os.environ.get("AGENT_CA_FILE", os.path.join(SSL_DIR, "agent-ca.crt"))

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

# ── Version / update checking ─────────────────────────────────────────────────
# Version = 1.0.<commit count>. The container has no .git, so the build bakes
# NETPULSE_GIT_COMMIT / NETPULSE_GIT_COUNT / NETPULSE_BUILT_AT (via docker build
# args); fall back to a live git call (dev on the host) then to unknown/0.

def _git(args, default=""):
    import subprocess
    try:
        return subprocess.run(
            ["git", *args], cwd=BASE_DIR, capture_output=True, text=True, timeout=3,
        ).stdout.strip() or default
    except Exception:
        return default

GIT_COMMIT = os.environ.get("NETPULSE_GIT_COMMIT") or _git(["rev-parse", "--short", "HEAD"], "unknown")
_GIT_COUNT = os.environ.get("NETPULSE_GIT_COUNT") or _git(["rev-list", "--count", "HEAD"], "0")
VERSION = f"1.0.{_GIT_COUNT}"
BUILT_AT = os.environ.get("NETPULSE_BUILT_AT", "")

# Update-check source. Repo is public, so no token is required; GITHUB_TOKEN is
# only needed for a private repo. VERSION_CHECK_ENABLED=false disables the check.
GITHUB_REPO = os.environ.get("GITHUB_REPO", "travisjohnsonga/netpulse")
GITHUB_BRANCH = os.environ.get("GITHUB_BRANCH", "main")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
VERSION_CHECK_ENABLED = os.environ.get("VERSION_CHECK_ENABLED", "true").lower() != "false"

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

# ── Hostname display ──────────────────────────────────────────────────────────
# Strip a domain suffix from device hostnames for DISPLAY ONLY (the stored
# hostname is still used for SSH/SNMP/syslog). These act as the platform default
# when no SystemSetting override is present; see apps.core.hostname.
STRIP_DOMAIN_FROM_HOSTNAMES = os.environ.get("STRIP_DOMAIN_FROM_HOSTNAMES", "false").lower() == "true"
DOMAIN_SUFFIX = os.environ.get("DOMAIN_SUFFIX", "")

# First-run setup gate. setup.sh sets SETUP_COMPLETE=true in .env when done;
# factory-reset.sh resets it to false. The frontend gates the whole app on
# GET /api/setup/status/ and shows the /setup welcome page until this is true.
SETUP_COMPLETE = os.environ.get("SETUP_COMPLETE", "false").lower() == "true"

# Publish device SNMP config to NATS (netpulse.devices.upsert) on save so the
# ingest-snmp poller learns about devices. Disabled in tests (no NATS).
SNMP_DEVICE_PUBLISH = os.environ.get("SNMP_DEVICE_PUBLISH", "true").lower() == "true"

# Write per-collector config bundles to the JetStream KV bucket on change
# (config-DOWN to remote collectors). Disabled in tests (no NATS); the local
# server still polls directly regardless.
COLLECTOR_CONFIG_PUBLISH = os.environ.get("COLLECTOR_CONFIG_PUBLISH", "true").lower() == "true"

# OpenBao PKI for per-collector mTLS *transport* certs (distinct from the
# operator/JWT *bus* identity). The intermediate CA lives at COLLECTOR_PKI_MOUNT,
# signed by a NetPulse collector root; the `collector` role issues client certs.
COLLECTOR_PKI_MOUNT = os.environ.get("COLLECTOR_PKI_MOUNT", "pki_int")
COLLECTOR_PKI_ROOT_MOUNT = os.environ.get("COLLECTOR_PKI_ROOT_MOUNT", "pki_root")
COLLECTOR_PKI_ROLE = os.environ.get("COLLECTOR_PKI_ROLE", "collector")
COLLECTOR_CERT_TTL = os.environ.get("COLLECTOR_CERT_TTL", "720h")

# The secret-broker MUST use its least-privilege AppRole in production; it will
# refuse to start (and refuse to read) rather than fall back to the platform
# token. Defaults to "required whenever DEBUG is false"; can be forced on.
BROKER_REQUIRE_APPROLE = os.environ.get(
    "BROKER_REQUIRE_APPROLE", str(not DEBUG)).lower() == "true"

# Rebuild the DiscoveredPlatformModel fleet inventory (OS-version compliance) on
# every Device save/delete. Disabled in tests to keep device-creation cheap; the
# scheduler refreshes it every 6h regardless.
OS_PLATFORM_REFRESH_ON_SAVE = os.environ.get("OS_PLATFORM_REFRESH_ON_SAVE", "true").lower() == "true"

# Auto-execute active-scan / topology discovery jobs in a background thread on
# creation (run_discovery). Disabled in tests so creating a job doesn't spawn a
# real network scan.
DISCOVERY_AUTORUN = os.environ.get("DISCOVERY_AUTORUN", "true").lower() == "true"

# After a discovered device is approved, enrich it in the background (SNMP/SSH
# for model/OS/serial/platform, then interface + LLDP discovery). Disabled in
# tests so approving a device never spawns a real probe.
DEVICE_AUTO_ENRICH = os.environ.get("DEVICE_AUTO_ENRICH", "true").lower() == "true"

# Directory of community-maintained vendor advisory YAML (Juniper/Arista/…),
# loaded by `load_community_advisories`. Mounted from the repo's advisories/.
COMMUNITY_ADVISORIES_DIR = os.environ.get("COMMUNITY_ADVISORIES_DIR", "/app/advisories")

# Hide endpoints/workstations (device_category="endpoint") from the discovered-
# devices list by default. They are still stored; the list endpoint shows them
# with ?show_all=true (or ?include_endpoints=true). Network devices and unknowns
# are never hidden.
DISCOVERY_FILTER_ENDPOINTS = os.environ.get("DISCOVERY_FILTER_ENDPOINTS", "true").lower() == "true"

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
    # No global throttle (avoids limiting health checks / normal API traffic);
    # the "auth" scope is applied only to the JWT token endpoints to slow
    # credential brute-force. Backed by the Valkey cache.
    "DEFAULT_THROTTLE_RATES": {
        "auth": os.environ.get("AUTH_THROTTLE_RATE", "10/min"),
    },
    # The API runs behind the frontend nginx (proxy_pass to api:8000). Without
    # this, DRF keys throttles on REMOTE_ADDR — which is the nginx container IP
    # for every client, collapsing the per-IP auth throttle into one shared
    # global bucket. NUM_PROXIES tells DRF to read the real client IP from the
    # X-Forwarded-For header (nginx must set it; see frontend nginx.conf). Set
    # NUM_PROXIES to the number of trusted proxies in front of the API.
    "NUM_PROXIES": int(os.environ.get("NUM_PROXIES", "1")),
}

# ChatOps inbound webhooks (Slack/Teams/Google Chat/Discord) are AllowAny — the
# platforms can't present a JWT — and most have no signature step, so an enabled
# webhook is an unauthenticated read into inventory/alert data. The feature is
# planned, not hardened, so it is DISABLED by default; enable explicitly once
# per-platform signature verification is enforced.
CHATOPS_ENABLED = os.environ.get("CHATOPS_ENABLED", "false").lower() == "true"

SPECTACULAR_SETTINGS = {
    "TITLE": "spane API",
    "DESCRIPTION": "spane — unified infrastructure visibility platform API",
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

# ── SSO / Single Sign-On (social-auth-app-django) ─────────────────────────────
# Local username/password stays as a fallback (ModelBackend) so an IdP outage
# can never lock everyone out. Provider client_id/secret are resolved per-request
# from the SSOProvider row + OpenBao by the Dynamic* backends (apps/sso/backends).
AUTHENTICATION_BACKENDS = [
    "apps.sso.backends.DynamicGoogleOAuth2",            # Google (DB+OpenBao creds)
    "apps.sso.backends.DynamicAzureADTenantOAuth2",     # Microsoft Azure AD (+ tenant)
    "apps.sso.backends.DynamicOktaOAuth2",              # Okta (+ okta_domain → API_URL)
    "apps.sso.backends.DynamicGithubOAuth2",            # GitHub
    "django.contrib.auth.backends.ModelBackend",        # local username/password fallback
]

SSO_ALLOW_LOCAL_LOGIN = os.environ.get("SSO_ALLOW_LOCAL_LOGIN", "true").lower() == "true"
SSO_DEFAULT_ROLE = os.environ.get("SSO_DEFAULT_ROLE", "viewer")
# Where the SPA lives; SSO mints a JWT and redirects here with it in the fragment.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "")

# Store social-auth extra data as JSON (Postgres + SQLite compatible).
SOCIAL_AUTH_JSONFIELD_ENABLED = True
# The API runs behind the frontend's HTTPS proxy (proxy_pass is plain http), so
# Django sees request.scheme == "http". Force the OAuth redirect_uri to https so
# it matches what the IdP has registered (NetPulse enforces HTTPS end-to-end).
SOCIAL_AUTH_REDIRECT_IS_HTTPS = os.environ.get("SSO_REDIRECT_IS_HTTPS", "true").lower() == "true"
# Let SocialAuthExceptionMiddleware turn pipeline exceptions into redirects.
SOCIAL_AUTH_RAISE_EXCEPTIONS = False
SOCIAL_AUTH_LOGIN_REDIRECT_URL = "/api/sso/jwt/"
SOCIAL_AUTH_LOGIN_ERROR_URL = "/api/sso/jwt/"

# Static fallbacks — the Dynamic* backends override these from the DB/OpenBao.
SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = os.environ.get("SOCIAL_AUTH_GOOGLE_OAUTH2_KEY", "")
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = os.environ.get("SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET", "")

# Azure AD uses the v2.0 endpoints (see DynamicAzureADTenantOAuth2). The v2
# default scope omits "email", which the domain-allowlist + profile-sync
# pipeline needs — request it explicitly (added to the backend's default scope).
SOCIAL_AUTH_AZUREAD_TENANT_OAUTH2_SCOPE = ["openid", "email", "profile"]

SOCIAL_AUTH_PIPELINE = (
    "social_core.pipeline.social_auth.social_details",
    "social_core.pipeline.social_auth.social_uid",
    "social_core.pipeline.social_auth.auth_allowed",
    "social_core.pipeline.social_auth.social_user",
    "social_core.pipeline.user.get_username",
    "apps.sso.pipeline.check_allowed_domain",      # custom: domain allowlist + signup gate
    "social_core.pipeline.user.create_user",
    "apps.sso.pipeline.assign_default_role",       # custom: default role for new users
    "social_core.pipeline.social_auth.associate_user",
    "social_core.pipeline.social_auth.load_extra_data",
    "social_core.pipeline.user.user_details",
    "apps.sso.pipeline.sync_user_profile",         # custom: sync name/email from IdP
)

# ── Localisation ──────────────────────────────────────────────────────────────

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ── Static ────────────────────────────────────────────────────────────────────

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Generated reports (apps.reports) are written under MEDIA_ROOT/reports/{y}/{m}/.
# Served only via the authenticated /api/reports/{id}/download/ endpoint — never
# exposed as a public static route.
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media")))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

WHITENOISE_USE_FINDERS = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
