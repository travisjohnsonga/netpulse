#!/bin/sh
# NetPulse API entrypoint — runs on every container start.
# Handles migrations and role seeding before handing off to the app process.
set -e

echo "[entrypoint] waiting for postgres..."
# pg_isready isn't available in the slim image; the depends_on healthcheck
# in docker-compose guarantees postgres is healthy before this container starts.

# Initialise + auto-unseal OpenBao (api service only — it mounts the data
# volume). Runs before migrations so secrets are reachable as the app starts.
if [ "$INIT_OPENBAO" = "1" ]; then
    echo "[entrypoint] initialising / unsealing OpenBao..."
    python manage.py init_openbao || echo "[entrypoint] OpenBao init/unseal had issues (continuing)"

    # Initialise the agent PKI (mount + CA + role + policy) and publish the CA
    # cert onto the shared ssl volume for nginx mTLS. Needs OpenBao (not the DB),
    # so run early — before migrations/seeds — so the CA is present when the
    # frontend (nginx) container boots. Idempotent; no-ops if OpenBao is sealed.
    echo "[entrypoint] setting up agent PKI..."
    python manage.py setup_agent_pki || echo "[entrypoint] agent PKI setup had issues (continuing)"
fi

echo "[entrypoint] running database migrations..."
python manage.py migrate --noinput

# Collect static assets (admin + DRF browsable API) for WhiteNoise to serve.
# Only the api service serves HTTP to users; other api-image services skip it.
if [ "$SEED_SUPERUSER" = "1" ]; then
    echo "[entrypoint] collecting static files..."
    python manage.py collectstatic --noinput || echo "[entrypoint] collectstatic had issues (continuing)"
fi

echo "[entrypoint] seeding role groups..."
python manage.py create_roles

# Superuser seeding runs ONLY in the api service (SEED_SUPERUSER=1). All
# api-image services share this entrypoint, but only the api should create the
# initial user. Idempotent: skips when the user already exists. The migrations
# above guarantee the auth table exists before this runs.
if [ "$SEED_SUPERUSER" = "1" ]; then
    echo "[entrypoint] ensuring superuser..."
    python manage.py ensure_superuser

    # Seed the default (system) alert rules so they appear on a fresh install.
    echo "[entrypoint] seeding default alert rules..."
    python manage.py seed_alert_rules || echo "[entrypoint] alert-rule seed had issues (continuing)"

    # Seed the default device roles so the role bubbles work on a fresh install.
    echo "[entrypoint] seeding default device roles..."
    python manage.py seed_device_roles || echo "[entrypoint] device-role seed had issues (continuing)"

    # Seed placeholder example sites (only when none exist) BEFORE hostname rules,
    # so the example "Site N devices" rules can resolve their site FK.
    echo "[entrypoint] seeding example sites..."
    python manage.py seed_sites || echo "[entrypoint] site seed had issues (continuing)"

    # Seed example hostname rules (disabled by default — admin reviews + enables).
    echo "[entrypoint] seeding example hostname rules..."
    python manage.py seed_hostname_rules || echo "[entrypoint] hostname-rule seed had issues (continuing)"

    # Seed example log filters (disabled by default — admin reviews + enables).
    echo "[entrypoint] seeding example log filters..."
    python manage.py seed_log_filters || echo "[entrypoint] log-filter seed had issues (continuing)"

    # Seed example compliance templates (disabled by default — admin reviews + enables).
    echo "[entrypoint] seeding example compliance templates..."
    python manage.py seed_compliance_templates || echo "[entrypoint] compliance-template seed had issues (continuing)"

    # Seed SSO providers from any SOCIAL_AUTH_* env vars (idempotent).
    echo "[entrypoint] seeding SSO providers from env..."
    python manage.py seed_sso_providers || echo "[entrypoint] SSO provider seed had issues (continuing)"

    # Register this server as the local collector (idempotent); the scheduler
    # keeps its heartbeat fresh thereafter.
    echo "[entrypoint] registering local collector..."
    python manage.py register_local_collector || echo "[entrypoint] local-collector registration had issues (continuing)"

    # Seed the ingest-snmp poller with the current device inventory (api only).
    # Best-effort: a NATS hiccup must not block startup.
    echo "[entrypoint] publishing device configs to NATS..."
    python manage.py publish_device_configs || echo "[entrypoint] device publish had issues (continuing)"

    # Load community advisory feeds (Juniper/Arista YAML) and correlate to devices.
    echo "[entrypoint] loading community advisories..."
    python manage.py load_community_advisories || echo "[entrypoint] advisory load had issues (continuing)"
fi

echo "[entrypoint] starting: $*"
exec "$@"
