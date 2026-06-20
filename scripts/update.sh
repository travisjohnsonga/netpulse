#!/usr/bin/env bash
#
# NetPulse / spane safe self-update.
#
# Pull origin/main, then update safely: snapshot a rollback point, back-fill new
# .env vars, back up the database, apply migrations explicitly, rebuild + restart
# the changed services, and verify health (with a rollback hint on failure).
#
# Usage:  ./netpulse.sh update      (or: bash scripts/update.sh [--yes])
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_DIR="$(pwd)"
UPDATE_LOG="$REPO_DIR/.update-history.log"   # repo-local (no root needed)

ts()   { date '+%H:%M:%S'; }
log()  { echo "[$(ts)] $1"; }
warn() { echo "[$(ts)] ⚠️  $1"; }
err()  { echo "[$(ts)] ❌ $1" >&2; }

ASSUME_YES=0
{ [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; } && ASSUME_YES=1

version_str() {
  local count commit
  count="$(git rev-list --count HEAD 2>/dev/null || echo 0)"
  commit="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "1.0.${count} (${commit})"
}

# ── Pre-flight ────────────────────────────────────────────────────────────────
echo "=================================================="
echo "  NetPulse Update"
echo "=================================================="
log "Current version: $(version_str)"

git fetch origin main --quiet
CURRENT="$(git rev-parse --short HEAD)"
LATEST="$(git rev-parse --short origin/main)"
if [ "$CURRENT" = "$LATEST" ]; then
  log "✅ Already up to date."
  exit 0
fi

BEHIND="$(git rev-list --count HEAD..origin/main)"
log "📦 Update available — ${BEHIND} commit(s) behind origin/main:"
git log --oneline "HEAD..origin/main" | head -20
echo ""
if [ "$ASSUME_YES" -ne 1 ]; then
  read -r -p "Apply update? [y/N]: " confirm
  case "${confirm:-}" in y|Y) ;; *) log "Update cancelled."; exit 0 ;; esac
fi

# Refuse to update a dirty tree (would block the ff-only pull and risks loss).
if ! git diff --quiet || ! git diff --cached --quiet; then
  err "Working tree has uncommitted changes. Commit/stash them first."
  exit 1
fi

# ── 1. Snapshot a rollback point ──────────────────────────────────────────────
SNAPSHOT_TAG="pre-update-$(date +%Y%m%d-%H%M%S)"
git tag "$SNAPSHOT_TAG" >/dev/null 2>&1 || true
log "Rollback point tagged: $SNAPSHOT_TAG"

# ── 2. Pull ───────────────────────────────────────────────────────────────────
log "Pulling latest code..."
git pull --ff-only origin main
CHANGED="$(git diff --name-only "$SNAPSHOT_TAG..HEAD" 2>/dev/null || echo '')"
log "New version: $(version_str)"

# ── 3. Back-fill new .env variables from .env.example ─────────────────────────
if [ -f .env ] && [ -f .env.example ]; then
  log "Checking for new environment variables..."
  added=0
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "${line// }" ]] && continue
    var="${line%%=*}"
    [[ "$var" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if ! grep -q "^${var}=" .env 2>/dev/null; then
      # Strip a trailing inline comment from the example default.
      default="${line#*=}"; default="${default%%#*}"
      default="${default%"${default##*[![:space:]]}"}"   # rtrim
      echo "${var}=${default}" >> .env
      warn "  added ${var}=${default} (review in .env)"
      added=$((added + 1))
    fi
  done < .env.example
  [ "$added" -eq 0 ] && log "  .env already has all variables."
fi

# Load DB creds for the backup (defaults match .env.example).
set -a; [ -f .env ] && . ./.env 2>/dev/null || true; set +a
PG_USER="${POSTGRES_USER:-netpulse}"; PG_DB="${POSTGRES_DB:-netpulse}"

# ── 4. Database backup before migrations ──────────────────────────────────────
BACKUP_FILE=""
if docker compose ps postgres --format '{{.Health}}' 2>/dev/null | grep -q healthy; then
  BACKUP_FILE="$REPO_DIR/.update-db-backup-$(date +%Y%m%d_%H%M%S).sql.gz"
  log "Backing up database → $(basename "$BACKUP_FILE")"
  if docker compose exec -T postgres pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$BACKUP_FILE"; then
    log "  database backed up."
  else
    warn "  database backup failed — continuing."
    rm -f "$BACKUP_FILE"; BACKUP_FILE=""
  fi
else
  warn "postgres not healthy — skipping pre-update DB backup."
fi

# ── 5. Rebuild images (version stamped via build args) ────────────────────────
log "Rebuilding API services..."
GIT_COMMIT="$(git rev-parse --short HEAD)" GIT_COUNT="$(git rev-list --count HEAD)" \
  BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" ./netpulse.sh rebuild-api

if echo "$CHANGED" | grep -q "^services/frontend/"; then
  log "Rebuilding frontend (frontend changed)..."
  ./netpulse.sh rebuild-frontend
else
  log "Frontend unchanged — skipping rebuild."
fi

# ── 6. Apply any pending migrations explicitly ────────────────────────────────
log "Applying database migrations..."
if docker compose exec -T api python manage.py migrate --noinput; then
  log "  migrations applied."
else
  err "Migrations failed. DB backup: ${BACKUP_FILE:-<none>}"
  err "Roll back with: ./netpulse.sh rollback   (snapshot $SNAPSHOT_TAG)"
  exit 1
fi

# ── 7. Re-apply Docker NAT (idempotent; may be lost after a reboot) ───────────
# shellcheck source=scripts/nat.sh
. "$REPO_DIR/scripts/nat.sh"
apply_docker_nat || warn "Could not re-apply Docker NAT — run: sudo ./netpulse.sh fix-nat"

# ── 8. Verify health ──────────────────────────────────────────────────────────
log "Verifying health..."
sleep 8
HEALTH="unreachable"
for _ in 1 2 3 4 5; do
  HEALTH="$(docker compose exec -T api python -c \
'import urllib.request,json,sys
try:
    sys.stdout.write(json.load(urllib.request.urlopen("http://localhost:8000/api/health/",timeout=5)).get("status","error"))
except Exception:
    sys.stdout.write("unreachable")' 2>/dev/null || echo unreachable)"
  [ "$HEALTH" = "ok" ] && break
  sleep 5
done

NEW_VERSION="$(version_str)"
if [ "$HEALTH" = "ok" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') | ${NEW_VERSION} | OK | from ${SNAPSHOT_TAG}" >> "$UPDATE_LOG"
  echo "=================================================="
  log "✅ Update complete — ${NEW_VERSION} (health: OK)"
  echo "=================================================="
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') | ${NEW_VERSION} | HEALTH=${HEALTH} | from ${SNAPSHOT_TAG}" >> "$UPDATE_LOG"
  err "Health check failed after update (status=${HEALTH})."
  err "DB backup: ${BACKUP_FILE:-<none>}"
  err "Roll back with: ./netpulse.sh rollback   (snapshot $SNAPSHOT_TAG)"
  exit 1
fi
