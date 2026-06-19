#!/usr/bin/env bash
#
# spane platform restore.
#
# Usage: scripts/restore.sh <backup-file.[enc.]tar.gz>
#
# Decrypts (if .enc.tar.gz), extracts, shows the manifest, and restores the
# PostgreSQL database. OpenBao / certs / config are extracted into the work dir
# for manual review/restore (restoring secrets in-place is intentionally a
# manual, deliberate step).
#
# The decryption password is read from BACKUP_PASSWORD if set, else prompted
# (silently). openssl reads it via `-pass env:BACKUP_PASSWORD` so it never
# appears in argv/ps.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

log()  { echo "[restore] $*" >&2; }
fail() { echo "$*" >&2; exit 1; }

dc() { ( cd "$COMPOSE_DIR" && docker compose "$@" ); }

BACKUP_FILE="${1:-}"
[ -n "$BACKUP_FILE" ] || fail "Usage: $0 <backup-file.[enc.]tar.gz>"
[ -f "$BACKUP_FILE" ] || fail "Backup file not found: ${BACKUP_FILE}"

PG_USER="${POSTGRES_USER:-netpulse}"
PG_DB="${POSTGRES_DB:-netpulse}"

cat >&2 <<EOF
============================================================
 spane RESTORE
   Source: ${BACKUP_FILE}
   This will OVERWRITE the current PostgreSQL database
   '${PG_DB}'. OpenBao/cert/config restore is manual.
============================================================
EOF
printf "Type 'yes' to continue: " >&2
read -r CONFIRM
[ "$CONFIRM" = "yes" ] || fail "Aborted."

WORK="$(mktemp -d /tmp/spane-restore.XXXXXX)"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

ARCHIVE="$BACKUP_FILE"

# ── decrypt if encrypted ─────────────────────────────────────────────────────
case "$BACKUP_FILE" in
  *.enc.tar.gz)
    if [ -z "${BACKUP_PASSWORD:-}" ]; then
      printf "Encryption password: " >&2
      read -rs BACKUP_PASSWORD
      echo >&2
      export BACKUP_PASSWORD
    fi
    log "decrypting archive"
    ARCHIVE="${WORK}/decrypted.tar.gz"
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
      -pass env:BACKUP_PASSWORD -in "$BACKUP_FILE" -out "$ARCHIVE" \
      || fail "Decryption failed (wrong password?)."
    ;;
esac

# ── extract ──────────────────────────────────────────────────────────────────
log "extracting"
tar -xzf "$ARCHIVE" -C "$WORK"
STAGE="$(find "$WORK" -maxdepth 1 -type d -name 'spane-backup-*' | head -1)"
[ -n "$STAGE" ] || fail "Could not locate backup contents in archive."

# ── manifest ─────────────────────────────────────────────────────────────────
if [ -f "${STAGE}/manifest.json" ]; then
  log "manifest:"
  cat "${STAGE}/manifest.json" >&2
fi

# ── postgres ─────────────────────────────────────────────────────────────────
if [ -f "${STAGE}/postgres.sql" ]; then
  log "restoring PostgreSQL database '${PG_DB}'"
  dc exec -T postgres psql -U "$PG_USER" -d "$PG_DB" < "${STAGE}/postgres.sql" \
    || fail "PostgreSQL restore failed."
  log "PostgreSQL restore complete."
fi

# ── secrets / certs / config — manual ────────────────────────────────────────
if [ -f "${STAGE}/openbao.snap" ]; then
  log "OpenBao snapshot present at ${STAGE}/openbao.snap"
  log "  restore with: docker compose exec -T openbao bao operator raft snapshot restore -force /tmp/openbao.snap"
fi
[ -d "${STAGE}/ssl" ]    && log "SSL certs extracted at ${STAGE}/ssl (restore manually into the api ssl volume)"
[ -d "${STAGE}/config" ] && log "Config files extracted at ${STAGE}/config (review before overwriting .env/compose)"

log "Restore finished. Extracted contents: ${STAGE}"
echo "$STAGE"
