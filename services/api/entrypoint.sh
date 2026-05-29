#!/bin/sh
# NetPulse API entrypoint — runs on every container start.
# Handles migrations and role seeding before handing off to the app process.
set -e

echo "[entrypoint] waiting for postgres..."
# pg_isready isn't available in the slim image; the depends_on healthcheck
# in docker-compose guarantees postgres is healthy before this container starts.

echo "[entrypoint] running database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] seeding role groups..."
python manage.py create_roles

echo "[entrypoint] starting: $*"
exec "$@"
