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

# Pre-update DB backups + the update history live OUTSIDE the git tree:
#  * in-tree backups false-positived update.sh's own "uncommitted changes"
#    guard on the next run (hit live on a customer box), and
#  * a delete-and-reinstall of the repo directory would destroy the very
#    backups meant to protect against needing one.
# /var/backups/<product> is the conventional Linux location; the directory is
# created on first use and chowned to the deploying user so later non-sudo
# runs can write without help (see ensure_backup_dir).
BACKUP_DIR="/var/backups/netpulse"
UPDATE_LOG="$BACKUP_DIR/update-history.log"

ts()   { date '+%H:%M:%S'; }
log()  { echo "[$(ts)] $1"; }
warn() { echo "[$(ts)] ⚠️  $1"; }
err()  { echo "[$(ts)] ❌ $1" >&2; }

ensure_backup_dir() {
  # Create $BACKUP_DIR writable by the deploying user. NEVER falls back to an
  # in-tree location and never silently skips — a permissions problem aborts
  # the update with instructions (a silent fallback would quietly reintroduce
  # the dirty-tree bug this fixes).
  local owner="${SUDO_USER:-$(id -un)}"
  if [ ! -d "$BACKUP_DIR" ]; then
    if ! mkdir -p "$BACKUP_DIR" 2>/dev/null; then
      # /var/backups is typically root-owned — try a non-interactive sudo.
      if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
        sudo -n mkdir -p "$BACKUP_DIR" && sudo -n chown "$owner" "$BACKUP_DIR"
      fi
    fi
  fi
  # Running under sudo: hand the dir to the invoking user for future runs.
  if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ -d "$BACKUP_DIR" ]; then
    chown "$SUDO_USER" "$BACKUP_DIR" 2>/dev/null || true
  fi
  if [ ! -d "$BACKUP_DIR" ] || [ ! -w "$BACKUP_DIR" ]; then
    err "cannot write backups to $BACKUP_DIR — pre-create it as root:"
    err "    sudo mkdir -p $BACKUP_DIR && sudo chown $owner $BACKUP_DIR"
    err "then re-run the update."
    exit 1
  fi
}
ensure_backup_dir

# One-time migration: sweep legacy in-tree backups/history into $BACKUP_DIR so
# they stop polluting `git status` and survive a repo reinstall.
for legacy in "$REPO_DIR"/.update-db-backup-*.sql.gz; do
  [ -e "$legacy" ] || continue
  mv "$legacy" "$BACKUP_DIR/$(basename "$legacy" | sed 's/^\.//')" \
    && log "migrated legacy backup $(basename "$legacy") → $BACKUP_DIR/"
done
if [ -f "$REPO_DIR/.update-history.log" ]; then
  cat "$REPO_DIR/.update-history.log" >> "$UPDATE_LOG" 2>/dev/null \
    && rm -f "$REPO_DIR/.update-history.log" \
    && log "migrated legacy .update-history.log → $UPDATE_LOG"
fi

ASSUME_YES=0
{ [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; } && ASSUME_YES=1

version_str() {
  # Report the SAME app-v* git-describe version the API/UI show (the badge and
  # /api/health/infrastructure/), NOT a disconnected commit-count scheme — a
  # customer seeing "1.0.905" here while the dashboard showed "0.7.0" for the
  # same deployment is the bug this fixes. Mirrors the non-env branch of the
  # API's resolver (services/api/config/settings/base.py::_app_version, from
  # PR #176): describe against app-v* tags, strip the prefix, and fall back to
  # 0.0.0+<hash> when no app-v* tag is reachable (fresh/shallow clone edge).
  local desc
  desc="$(git describe --tags --match 'app-v*' --always --dirty 2>/dev/null || echo '')"
  case "$desc" in
    app-v*) echo "${desc#app-v}" ;;
    "")     echo "0.0.0+$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" ;;
    *)      echo "0.0.0+${desc}" ;;   # bare hash: no app-v* tag reachable
  esac
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

# Stamp the precise APP version into .env so /api/health/ reports it. MUST
# match against app-v* tags only: a bare `git describe --tags` can resolve to
# an AGENT tag (v1.5.0-…) and, since SPANE_VERSION is the runtime override
# (see settings/base.py _app_version), a wrong stamp here would override the
# correct build-baked version.
NEW_VER="$(git describe --tags --match 'app-v*' 2>/dev/null | sed 's/^app-v//')"
[ -n "$NEW_VER" ] || NEW_VER="$(git rev-parse --short HEAD)"
if [ -f .env ]; then
  if grep -q '^SPANE_VERSION=' .env; then
    sed -i "s|^SPANE_VERSION=.*|SPANE_VERSION=${NEW_VER}|" .env
  else
    echo "SPANE_VERSION=${NEW_VER}" >> .env
  fi
  log "Stamped SPANE_VERSION=${NEW_VER}"
fi

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
  # Non-hidden name — it no longer lives in the repo, so visibility beats tidiness.
  BACKUP_FILE="$BACKUP_DIR/update-db-backup-$(date +%Y%m%d_%H%M%S).sql.gz"
  log "Backing up database → $BACKUP_FILE"
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
# Probe the REAL public endpoint (nginx :443, self-signed → -k) — NOT :8000.
# With SECURE_SSL_REDIRECT=true (the shipped production default) a plain-HTTP
# probe of gunicorn's :8000 gets a 301 to https://…:8000, which gunicorn can't
# serve → the old check reported a FALSE "unreachable" (with rollback advice)
# after perfectly successful updates. Going through nginx also verifies the
# full path a customer actually uses (frontend up + proxy wired + api up).
# 10 attempts × 5s on top of the initial settle covers slow container starts;
# a genuine failure still exhausts the retries and keeps the rollback guidance.
log "Verifying health..."
sleep 8
HTTPS_PORT="${FRONTEND_HTTPS_PORT:-443}"   # .env already sourced above
HEALTH="unreachable"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  body="$(curl -sk --max-time 5 "https://localhost:${HTTPS_PORT}/api/health/" 2>/dev/null || true)"
  HEALTH="$(printf '%s' "$body" | python3 -c \
'import json,sys
try:
    sys.stdout.write(json.load(sys.stdin).get("status","error"))
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
