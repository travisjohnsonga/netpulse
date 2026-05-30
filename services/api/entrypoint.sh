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
fi

echo "[entrypoint] running database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] seeding role groups..."
python manage.py create_roles

# Superuser seeding runs ONLY in the api service (SEED_SUPERUSER=1). All
# api-image services share this entrypoint, but only the api should create the
# initial user. Idempotent: skips when the user already exists. The migrations
# above guarantee the auth table exists before this runs.
if [ "$SEED_SUPERUSER" = "1" ]; then
    echo "[entrypoint] ensuring superuser..."
    python manage.py ensure_superuser

    # Seed the ingest-snmp poller with the current device inventory (api only).
    # Best-effort: a NATS hiccup must not block startup.
    echo "[entrypoint] publishing device configs to NATS..."
    python manage.py publish_device_configs || echo "[entrypoint] device publish had issues (continuing)"
fi

echo "[entrypoint] starting: $*"
exec "$@"
