#!/usr/bin/env bash
#
# spane platform backup.
#
# Produces an encrypted (when secrets are included) tar.gz of the selected
# components, a PLAINTEXT manifest alongside it, optionally uploads to a remote
# destination, prunes old backups, and prints the final archive path on stdout.
#
# Configuration is entirely via environment variables (the API/scheduler sets
# these; never passes secrets as args):
#   BACKUP_POSTGRES / BACKUP_OPENBAO / BACKUP_CONFIG / BACKUP_CERTS / BACKUP_INFLUXDB
#       — "true"/"false" component toggles
#   BACKUP_INFLUXDB_DAYS    — retention window for the influx export (informational)
#   BACKUP_LOCAL_PATH       — where the final artifact is written (default /opt/spane/backups)
#   BACKUP_DEST             — local | scp | git | s3
#   BACKUP_RETENTION_DAYS   — prune local backups older than this many days
#   BACKUP_PASSWORD         — encryption password (MANDATORY when secrets included);
#                             read via `openssl -pass env:BACKUP_PASSWORD` so it
#                             never appears in argv/ps
#   SCP_*  / GIT_* / S3_*   — destination connection settings (+ secrets via env)
#
set -euo pipefail

# Repo / compose directory (this script lives in <repo>/scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── helpers ──────────────────────────────────────────────────────────────────
is_true() { [ "${1:-false}" = "true" ]; }
log()     { echo "[backup] $*" >&2; }
fail()    { echo "$*" >&2; exit 1; }

dc() {
  # Run docker compose from the repo dir.
  ( cd "$COMPOSE_DIR" && docker compose "$@" )
}

# ── config / defaults ────────────────────────────────────────────────────────
BACKUP_POSTGRES="${BACKUP_POSTGRES:-true}"
BACKUP_OPENBAO="${BACKUP_OPENBAO:-true}"
BACKUP_CONFIG="${BACKUP_CONFIG:-true}"
BACKUP_CERTS="${BACKUP_CERTS:-true}"
BACKUP_INFLUXDB="${BACKUP_INFLUXDB:-false}"
BACKUP_INFLUXDB_DAYS="${BACKUP_INFLUXDB_DAYS:-30}"
BACKUP_LOCAL_PATH="${BACKUP_LOCAL_PATH:-/opt/spane/backups}"
BACKUP_DEST="${BACKUP_DEST:-local}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_PASSWORD="${BACKUP_PASSWORD:-}"

PG_USER="${POSTGRES_USER:-netpulse}"
PG_DB="${POSTGRES_DB:-netpulse}"

# ── mandatory-encryption gate (BEFORE writing anything) ──────────────────────
# If any sensitive component is selected and no password is set, refuse.
SECRETS_INCLUDED="false"
if is_true "$BACKUP_OPENBAO" || is_true "$BACKUP_CERTS" || is_true "$BACKUP_POSTGRES" \
   || [ "${INCLUDE_SECRETS:-false}" = "true" ]; then
  SECRETS_INCLUDED="true"
fi
if [ "$SECRETS_INCLUDED" = "true" ] && [ -z "$BACKUP_PASSWORD" ]; then
  fail "Refusing to create an unencrypted backup that includes secrets (OpenBao/SSL/Postgres). Set BACKUP_PASSWORD."
fi

# ── work dir ─────────────────────────────────────────────────────────────────
TS="$(date -u +%Y%m%d-%H%M%S)"
NAME="spane-backup-${TS}"
WORK="$(mktemp -d "/tmp/${NAME}.XXXXXX")"
STAGE="${WORK}/${NAME}"
mkdir -p "$STAGE"
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

COMPONENTS=()

# ── postgres ─────────────────────────────────────────────────────────────────
if is_true "$BACKUP_POSTGRES"; then
  log "dumping PostgreSQL database '${PG_DB}'"
  dc exec -T postgres pg_dump -U "$PG_USER" -d "$PG_DB" > "${STAGE}/postgres.sql" \
    || fail "pg_dump failed"
  COMPONENTS+=("postgres")
fi

# ── openbao (raft snapshot) ──────────────────────────────────────────────────
if is_true "$BACKUP_OPENBAO"; then
  log "saving OpenBao raft snapshot"
  # `bao` (not `vault`); write to a path inside the container then copy out.
  if dc exec -T openbao bao operator raft snapshot save /tmp/openbao.snap >/dev/null 2>&1; then
    dc cp openbao:/tmp/openbao.snap "${STAGE}/openbao.snap" >/dev/null 2>&1 \
      || dc exec -T openbao cat /tmp/openbao.snap > "${STAGE}/openbao.snap"
    dc exec -T openbao rm -f /tmp/openbao.snap >/dev/null 2>&1 || true
  else
    # Non-raft storage (file backend): snapshot unsupported — note it.
    log "raft snapshot unavailable (non-raft storage); skipping OpenBao snapshot"
  fi
  COMPONENTS+=("openbao")
fi

# ── influxdb (optional) ──────────────────────────────────────────────────────
if is_true "$BACKUP_INFLUXDB"; then
  log "exporting InfluxDB (last ${BACKUP_INFLUXDB_DAYS}d)"
  mkdir -p "${STAGE}/influxdb"
  # Best-effort: influx backup into a container path then copy out.
  if dc exec -T influxdb influx backup /tmp/influx-backup >/dev/null 2>&1; then
    dc cp influxdb:/tmp/influx-backup "${STAGE}/influxdb" >/dev/null 2>&1 || true
    dc exec -T influxdb rm -rf /tmp/influx-backup >/dev/null 2>&1 || true
  else
    log "influx backup unavailable; skipping InfluxDB export"
  fi
  COMPONENTS+=("influxdb")
fi

# ── config files ─────────────────────────────────────────────────────────────
if is_true "$BACKUP_CONFIG"; then
  log "copying config files (.env, docker-compose.yml, nginx.conf)"
  mkdir -p "${STAGE}/config"
  [ -f "${COMPOSE_DIR}/.env" ] && cp "${COMPOSE_DIR}/.env" "${STAGE}/config/.env" || true
  [ -f "${COMPOSE_DIR}/docker-compose.yml" ] && cp "${COMPOSE_DIR}/docker-compose.yml" "${STAGE}/config/docker-compose.yml" || true
  [ -f "${COMPOSE_DIR}/services/frontend/nginx.conf" ] \
    && cp "${COMPOSE_DIR}/services/frontend/nginx.conf" "${STAGE}/config/nginx.conf" || true
  COMPONENTS+=("config")
fi

# ── ssl certs (from the api container) ───────────────────────────────────────
if is_true "$BACKUP_CERTS"; then
  log "copying SSL certs from api:/app/ssl"
  mkdir -p "${STAGE}/ssl"
  dc cp api:/app/ssl "${STAGE}/" >/dev/null 2>&1 || log "no ssl dir in api container (skipping)"
  COMPONENTS+=("certs")
fi

# ── manifest (PLAINTEXT, alongside the archive) ──────────────────────────────
ENCRYPTED="false"
[ -n "$BACKUP_PASSWORD" ] && ENCRYPTED="true"
COMPONENTS_JSON="$(printf '"%s",' "${COMPONENTS[@]:-}" | sed 's/,$//')"
HINT="${BACKUP_PASSWORD_HINT:-}"

write_manifest() {
  local dest="$1"
  cat > "$dest" <<EOF
{
  "backup_name": "${NAME}",
  "timestamp": "${TS}",
  "encrypted": ${ENCRYPTED},
  "encryption_hint": "${HINT}",
  "algorithm": "AES-256-CBC-PBKDF2",
  "components": [${COMPONENTS_JSON}]
}
EOF
}

# A copy of the manifest goes INSIDE the archive too (self-describing).
write_manifest "${STAGE}/manifest.json"

# ── tar + gzip ───────────────────────────────────────────────────────────────
mkdir -p "$BACKUP_LOCAL_PATH"
PLAIN_ARCHIVE="${WORK}/${NAME}.tar.gz"
log "creating archive"
tar -czf "$PLAIN_ARCHIVE" -C "$WORK" "$NAME"

# ── encryption (mandatory when secrets included) ─────────────────────────────
if [ -n "$BACKUP_PASSWORD" ]; then
  log "encrypting archive (AES-256-CBC, PBKDF2 600000 iters)"
  FINAL="${BACKUP_LOCAL_PATH}/${NAME}.enc.tar.gz"
  openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
    -pass env:BACKUP_PASSWORD -in "$PLAIN_ARCHIVE" -out "$FINAL"
  rm -f "$PLAIN_ARCHIVE"
else
  FINAL="${BACKUP_LOCAL_PATH}/${NAME}.tar.gz"
  mv "$PLAIN_ARCHIVE" "$FINAL"
fi

# Plaintext manifest alongside the final artifact (so contents are visible
# without the password).
write_manifest "${BACKUP_LOCAL_PATH}/${NAME}.manifest.json"

# ── remote upload (guarded by BACKUP_DEST) ───────────────────────────────────
upload_scp() {
  [ -n "${SCP_HOST:-}" ] || { log "scp: no host; skipping upload"; return 0; }
  local target="${SCP_USERNAME:+${SCP_USERNAME}@}${SCP_HOST}:${SCP_PATH:-.}"
  log "uploading to scp ${SCP_HOST}"
  local ssh_opts=(-P "${SCP_PORT:-22}" -o StrictHostKeyChecking=accept-new)
  if [ -n "${SCP_SSH_KEY:-}" ]; then
    local keyfile; keyfile="$(mktemp)"; chmod 600 "$keyfile"
    printf '%s\n' "$SCP_SSH_KEY" > "$keyfile"
    scp "${ssh_opts[@]}" -i "$keyfile" "$FINAL" "${BACKUP_LOCAL_PATH}/${NAME}.manifest.json" "$target" || { rm -f "$keyfile"; log "scp upload failed"; return 1; }
    rm -f "$keyfile"
  elif [ -n "${SCP_PASSWORD:-}" ] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$SCP_PASSWORD" sshpass -e scp "${ssh_opts[@]}" "$FINAL" "${BACKUP_LOCAL_PATH}/${NAME}.manifest.json" "$target" || { log "scp upload failed"; return 1; }
  else
    scp "${ssh_opts[@]}" "$FINAL" "${BACKUP_LOCAL_PATH}/${NAME}.manifest.json" "$target" || { log "scp upload failed"; return 1; }
  fi
}

upload_git() {
  [ -n "${GIT_REPO_URL:-}" ] || { log "git: no repo; skipping"; return 0; }
  log "pushing to git ${GIT_REPO_URL}"
  local gdir; gdir="$(mktemp -d)"
  local git_env=()
  if [ -n "${GIT_SSH_KEY:-}" ]; then
    local keyfile; keyfile="$(mktemp)"; chmod 600 "$keyfile"
    printf '%s\n' "$GIT_SSH_KEY" > "$keyfile"
    export GIT_SSH_COMMAND="ssh -i ${keyfile} -o StrictHostKeyChecking=accept-new"
  fi
  ( git clone --depth 1 --branch "${GIT_BRANCH:-main}" "$GIT_REPO_URL" "$gdir" 2>/dev/null \
      || git clone --depth 1 "$GIT_REPO_URL" "$gdir" ) || { log "git clone failed"; rm -rf "$gdir"; return 1; }
  mkdir -p "${gdir}/${GIT_PATH:-spane/}"
  cp "$FINAL" "${BACKUP_LOCAL_PATH}/${NAME}.manifest.json" "${gdir}/${GIT_PATH:-spane/}"
  ( cd "$gdir" && git add -A && git -c user.email=backup@spane -c user.name=spane commit -m "backup ${NAME}" \
      && git push origin "${GIT_BRANCH:-main}" ) || { log "git push failed"; rm -rf "$gdir"; return 1; }
  rm -rf "$gdir"
}

upload_s3() {
  [ -n "${S3_BUCKET:-}" ] || { log "s3: no bucket; skipping"; return 0; }
  command -v aws >/dev/null 2>&1 || { log "s3: aws cli not installed; skipping"; return 0; }
  log "uploading to s3://${S3_BUCKET}/${S3_PREFIX:-spane-backups/}"
  local extra=()
  [ -n "${S3_ENDPOINT:-}" ] && extra+=(--endpoint-url "$S3_ENDPOINT")
  [ -n "${S3_REGION:-}" ] && extra+=(--region "$S3_REGION")
  AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-${AWS_ACCESS_KEY_ID:-}}" \
  AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-${AWS_SECRET_ACCESS_KEY:-}}" \
    aws s3 cp "$FINAL" "s3://${S3_BUCKET}/${S3_PREFIX:-spane-backups/}$(basename "$FINAL")" "${extra[@]}" \
      || { log "s3 upload failed"; return 1; }
  AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-${AWS_ACCESS_KEY_ID:-}}" \
  AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-${AWS_SECRET_ACCESS_KEY:-}}" \
    aws s3 cp "${BACKUP_LOCAL_PATH}/${NAME}.manifest.json" \
      "s3://${S3_BUCKET}/${S3_PREFIX:-spane-backups/}${NAME}.manifest.json" "${extra[@]}" || true
}

case "$BACKUP_DEST" in
  scp) upload_scp ;;
  git) upload_git ;;
  s3)  upload_s3 ;;
  local|"") : ;;
  *) log "unknown BACKUP_DEST '${BACKUP_DEST}'; keeping local only" ;;
esac

# ── retention cleanup (local) ────────────────────────────────────────────────
if [ "${BACKUP_RETENTION_DAYS:-0}" -gt 0 ] 2>/dev/null; then
  log "pruning backups older than ${BACKUP_RETENTION_DAYS}d in ${BACKUP_LOCAL_PATH}"
  find "$BACKUP_LOCAL_PATH" -maxdepth 1 -type f \
    \( -name 'spane-backup-*.tar.gz' -o -name 'spane-backup-*.manifest.json' \) \
    -mtime "+${BACKUP_RETENTION_DAYS}" -delete 2>/dev/null || true
fi

log "backup complete: ${FINAL}"
# Echo the final archive path on stdout (LAST line — the runner reads it).
echo "$FINAL"
