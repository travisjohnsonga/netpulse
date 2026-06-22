#!/bin/bash
# spane watchdog — monitors and auto-recovers unhealthy services.
# Install via: ./netpulse.sh install-watchdog   (cron, every 5 minutes)
#
# Adapted to this stack's real topology:
#   * Infra services (incl. OpenBao) are internal-only on the netpulse-net
#     bridge — they have NO host ports — so seal-status is queried via
#     `docker compose exec`, not a host curl to :8200.
#   * gunicorn runs as the api CONTAINER's main process, not a host process, so
#     the fd check counts fds inside the container, not via host pgrep.
#
# Deliberately NOT `set -e`: a watchdog must keep running through a single
# failed probe/restart rather than abort mid-recovery. (-u/pipefail are kept.)
set -uo pipefail

COMPOSE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="${SPANE_WATCHDOG_LOG:-/var/log/spane-watchdog.log}"
MAX_FD_WARN="${SPANE_WATCHDOG_MAX_FD:-50000}"
API_PORT="${API_PORT:-8000}"

log() {
    local line="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$line" | tee -a "$LOG_FILE" 2>/dev/null || echo "$line"
}

cd "$COMPOSE_DIR" || { echo "watchdog: cannot cd to $COMPOSE_DIR" >&2; exit 1; }

# Grace window (seconds) during which a freshly (re)started api is considered to
# be still booting — migrations, OpenBao unseal, gunicorn warm-up — and so an
# "unhealthy"/non-ok status is NOT treated as broken.
API_STARTUP_GRACE_S="${SPANE_WATCHDOG_API_GRACE_S:-120}"

# Seconds since the api container last (re)started, or empty if it can't be
# determined (e.g. container missing). Resolves the container id via compose so
# it works regardless of the project/container-name prefix.
api_uptime_s() {
    local cid started started_s now_s
    cid=$(docker compose ps -q api 2>/dev/null)
    [ -z "$cid" ] && return 1
    started=$(docker inspect "$cid" --format '{{.State.StartedAt}}' 2>/dev/null)
    [ -z "$started" ] && return 1
    started_s=$(date -d "$started" +%s 2>/dev/null) || return 1
    now_s=$(date +%s)
    echo $(( now_s - started_s ))
}

# ── Critical container health ────────────────────────────────────────────────
CRITICAL_SERVICES=(api postgres openbao valkey nats influxdb opensearch)
for service in "${CRITICAL_SERVICES[@]}"; do
    STATE=$(docker compose ps "$service" --format '{{.State}}' 2>/dev/null || echo "")
    [ -z "$STATE" ] && STATE="missing"
    HEALTH=$(docker compose ps "$service" --format '{{.Health}}' 2>/dev/null || echo "")

    if [ "$STATE" = "exited" ] || [ "$STATE" = "dead" ] || [ "$STATE" = "missing" ]; then
        log "CRITICAL: $service is $STATE — starting"
        docker compose start "$service" 2>/dev/null \
            || docker compose up -d "$service" 2>/dev/null || true
        sleep 10
    elif [ "$HEALTH" = "unhealthy" ]; then
        if [ "$service" = "api" ]; then
            UPTIME_S=$(api_uptime_s)
            if [ -n "$UPTIME_S" ] && [ "$UPTIME_S" -lt "$API_STARTUP_GRACE_S" ] 2>/dev/null; then
                log "api unhealthy but only started ${UPTIME_S}s ago — waiting (grace ${API_STARTUP_GRACE_S}s)"
                continue
            fi
        fi
        log "WARNING: $service unhealthy — restarting"
        docker compose restart "$service" 2>/dev/null || true
        sleep 10
    fi
done

# ── API health endpoint ──────────────────────────────────────────────────────
# api exposes ${API_PORT} on the host; GET /api/health/ → {"status": "ok", ...}.
check_api() {
    curl -sf --max-time 5 "http://localhost:${API_PORT}/api/health/" 2>/dev/null \
        | python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("status", "unknown"))
except Exception:
    print("error")' 2>/dev/null || echo "unreachable"
}

API_STATUS=$(check_api)
if [ "$API_STATUS" != "ok" ]; then
    # Don't restart an api that's still inside its startup grace window — a
    # freshly (re)started container reports non-ok while it boots (migrations,
    # OpenBao unseal, gunicorn warm-up). Restarting now would loop it forever.
    UPTIME_S=$(api_uptime_s)
    if [ -n "$UPTIME_S" ] && [ "$UPTIME_S" -lt "$API_STARTUP_GRACE_S" ] 2>/dev/null; then
        log "API unhealthy (health=$API_STATUS) but only started ${UPTIME_S}s ago — waiting (grace ${API_STARTUP_GRACE_S}s)"
        log "Watchdog OK (api=$API_STATUS, starting)"
        exit 0
    fi
    log "WARNING: API health=$API_STATUS — restarting api"
    docker compose restart api 2>/dev/null || true
    sleep 20
    API_STATUS=$(check_api)
    if [ "$API_STATUS" != "ok" ]; then
        log "CRITICAL: API still $API_STATUS after restart — checking OpenBao seal"
        # OpenBao has no host port → query the seal status via the container.
        SEALED=$( (docker compose exec -T openbao bao status -format=json 2>/dev/null || true) \
            | python3 -c 'import sys, json
try:
    print(json.load(sys.stdin).get("sealed", "unknown"))
except Exception:
    print("unknown")' 2>/dev/null || echo "unknown")
        if [ "$SEALED" = "True" ] || [ "$SEALED" = "true" ]; then
            log "OpenBao sealed — unsealing via init_openbao"
            docker compose exec -T api python manage.py init_openbao >> "$LOG_FILE" 2>&1 || true
            sleep 5
            docker compose restart api 2>/dev/null || true
            sleep 20
            log "Post-unseal API status: $(check_api)"
        fi
    else
        log "API recovered"
    fi
fi

# ── File-descriptor leak guard (inside the api container) ────────────────────
# gunicorn worker recycling (compose --max-requests) is the primary defence;
# this is a coarse backstop that restarts api if open fds run away.
FD_COUNT=$(docker compose exec -T api sh -c 'ls /proc/[0-9]*/fd 2>/dev/null | wc -l' 2>/dev/null \
    | tr -cd '0-9')
if [ -n "$FD_COUNT" ] && [ "$FD_COUNT" -gt "$MAX_FD_WARN" ] 2>/dev/null; then
    log "WARNING: api fd count=$FD_COUNT > $MAX_FD_WARN — preemptive restart"
    docker compose restart api 2>/dev/null || true
    sleep 20
    log "Preemptive restart complete"
fi

log "Watchdog OK (api=$API_STATUS${FD_COUNT:+, fds=$FD_COUNT})"
