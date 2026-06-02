#!/usr/bin/env bash
#
# factory-reset.sh — DEVELOPMENT tool to wipe NetPulse data and start fresh.
#
# DESTRUCTIVE AND IRREVERSIBLE. Never wire this into CI or any automated
# pipeline. Multiple safety gates guard it:
#   1. requires --confirm (no accidental runs)
#   2. requires ALLOW_FACTORY_RESET=true in .env
#   3. warns if the environment looks like production
#   4. requires typing RESET at the prompt
#
# Usage:
#   ./scripts/factory-reset.sh                 # show help
#   ./scripts/factory-reset.sh --confirm       # full reset (wipe data volumes)
#   ./scripts/factory-reset.sh --soft --confirm # data-only reset (keep volumes)
#
set -euo pipefail

cd "$(dirname "$0")/.."

SOFT=false
CONFIRM=false
for arg in "$@"; do
  case "$arg" in
    --soft) SOFT=true ;;
    --confirm) CONFIRM=true ;;
    -h|--help) CONFIRM=false ;;
    *) echo "Unknown argument: $arg"; exit 2 ;;
  esac
done

red()    { printf '\033[31m%s\033[0m\n' "$1"; }
green()  { printf '\033[32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[33m%s\033[0m\n' "$1"; }

if [ "$CONFIRM" != true ]; then
  cat <<'EOF'
NetPulse factory reset (DEVELOPMENT ONLY)

This permanently deletes all NetPulse data. It is irreversible.

Usage:
  ./scripts/factory-reset.sh --confirm          Full reset — wipe data volumes
  ./scripts/factory-reset.sh --soft --confirm   Soft reset — clear app data, keep volumes

The --confirm flag is required; you will then be asked to type RESET.
Requires ALLOW_FACTORY_RESET=true in .env.
EOF
  exit 0
fi

# ── Pre-flight checks ─────────────────────────────────────────────────────────
if ! docker compose ps >/dev/null 2>&1; then
  red "docker compose is not available in this directory. Aborting."
  exit 1
fi

if [ ! -f .env ]; then
  red ".env not found. Aborting."
  exit 1
fi

# Extra production guard.
if ! grep -qE '^ALLOW_FACTORY_RESET=true$' .env; then
  red "ALLOW_FACTORY_RESET is not true in .env — refusing to run."
  yellow "Set ALLOW_FACTORY_RESET=true in .env to allow factory reset (never in production)."
  exit 1
fi

# Heuristic production warning: a non-RFC1918 / non-localhost COLLECTOR_IP.
COLLECTOR_IP=$(grep -E '^COLLECTOR_IP=' .env | head -1 | cut -d= -f2- | tr -d '"')
if [ -n "${COLLECTOR_IP:-}" ] && \
   ! echo "$COLLECTOR_IP" | grep -qE '^(127\.|10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.|localhost|0\.0\.0\.0)'; then
  yellow "⚠  COLLECTOR_IP=$COLLECTOR_IP does not look like a private/dev address."
  yellow "   If this is production, STOP NOW (Ctrl-C)."
fi

# ── Warning banner ────────────────────────────────────────────────────────────
cat <<'EOF'
╔══════════════════════════════════════════════╗
║          ⚠️  FACTORY RESET WARNING            ║
╠══════════════════════════════════════════════╣
║  This will permanently delete:               ║
║                                              ║
║  • All devices and credentials               ║
║  • All telemetry data (InfluxDB)             ║
║  • All logs (OpenSearch)                      ║
║  • All config backups                        ║
║  • All alerts and events                     ║
║  • All topology links                        ║
║  • All service checks                        ║
║  • All users (except admin, re-seeded)       ║
║  • OpenBao secrets (full reset only)         ║
║                                              ║
║  THIS CANNOT BE UNDONE.                       ║
╚══════════════════════════════════════════════╝
EOF
if [ "$SOFT" = true ]; then
  yellow "Mode: SOFT reset — data volumes kept, app data cleared."
else
  yellow "Mode: FULL reset — postgres/influxdb/opensearch/openbao/nats/valkey volumes WIPED (ssl-certs kept)."
fi

# ── Explicit confirmation ─────────────────────────────────────────────────────
printf 'Type RESET to confirm: '
read -r confirmation
if [ "$confirmation" != "RESET" ]; then
  red "Aborted."
  exit 1
fi

wait_for_api() {
  echo "Waiting for API to become healthy..."
  until curl -sf http://localhost:8000/api/health/ >/dev/null 2>&1; do
    echo "  waiting for API..."
    sleep 5
  done
}

if [ "$SOFT" = true ]; then
  # ── Soft reset: clear app data, keep volumes ────────────────────────────────
  echo "Clearing application data (keeping auth users/groups)..."
  docker compose exec -T api python manage.py reset_test_data
  echo "Re-seeding admin user..."
  docker compose exec -T api python manage.py ensure_superuser || true
  green "✅ Soft reset complete!"
else
  # ── Full reset: wipe data volumes ───────────────────────────────────────────
  echo "Stopping all services..."
  docker compose down

  echo "Deleting data volumes (ssl-certs preserved)..."
  for v in postgres-data influxdb-data opensearch-data openbao-data nats-data valkey-data; do
    docker volume rm "netpulse_${v}" 2>/dev/null && echo "  removed netpulse_${v}" || echo "  netpulse_${v} not present"
  done

  echo "Starting fresh..."
  docker compose up -d
  echo "Waiting for services to initialize..."
  sleep 30
  wait_for_api
  green "✅ Factory reset complete!"

  # Reset the first-run flag so the UI shows the /setup page until setup.sh runs.
  if grep -q "SETUP_COMPLETE" .env 2>/dev/null; then
    sed -i 's/SETUP_COMPLETE=true/SETUP_COMPLETE=false/' .env
    echo "Reset SETUP_COMPLETE=false in .env"
  fi

  echo ""
  echo "=================================================="
  echo "  !! REQUIRED: Run setup.sh before using NetPulse"
  echo "=================================================="
  echo ""
  echo "  ./scripts/setup.sh"
  echo ""
  echo "  This is NOT optional. Without it, .env is missing"
  echo "  required secrets and:"
  echo "  - Infrastructure services (Postgres/InfluxDB/"
  echo "    OpenSearch/NATS) won't authenticate or start"
  echo "  - No admin account exists to log in"
  echo "  - The API will fail to boot"
  echo ""
  echo "  After setup.sh completes:"
  echo "  ./netpulse.sh rebuild-api"
  echo "=================================================="
fi

echo "Access:  http://localhost:3000  (redirects to https://localhost:3443)"
echo "Login:   admin / (DJANGO_SUPERUSER_PASSWORD from .env)"
