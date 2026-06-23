#!/bin/bash
# NetPulse management script

cd "$(dirname "$0")"

# All services built from ./services/api (they share one image). rebuild-api
# rebuilds that image once and recreates each with --no-deps so infrastructure
# (postgres, nats, …) is left untouched.
API_SERVICES="api websocket config-manager scheduler alert-engine cve-engine lifecycle-engine security-engine stream-processor check-engine reachability-monitor"

# Version stamped into the api image at build time (the image has no .git).
# Exported so docker-compose's api build.args pick them up on any build below.
if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
  export GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null)"
  export GIT_COUNT="$(git rev-list --count HEAD 2>/dev/null)"
  export BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi

case "$1" in
  start)
    echo "Starting NetPulse..."
    docker compose up -d
    sleep 5
    docker compose ps
    ;;
  stop)
    echo "Stopping NetPulse..."
    # `--profile "*"` activates every profile so `down` also tears down
    # profile-gated containers (e.g. the llm/ollama profile) that are no longer
    # active after COMPOSE_PROFILES is blanked — `--remove-orphans` alone does NOT
    # remove them (a profile-disabled service is still defined, so it's not an
    # orphan). --remove-orphans additionally clears services dropped from compose.
    docker compose --profile "*" down --remove-orphans
    ;;
  restart)
    echo "Restarting NetPulse..."
    # See `stop`: `--profile "*"` + --remove-orphans so a profile disabled since
    # the last start (e.g. llm/ollama) is torn down on restart, not left running.
    # The `up` below uses the active COMPOSE_PROFILES, so a disabled profile stays
    # down.
    docker compose --profile "*" down --remove-orphans
    # Rebuild the code-bearing images (all api services + frontend) BEFORE `up` so
    # a `git pull` + restart actually ships merged backend code/migrations and UI.
    # Without this, `up` recreates from the existing image and merged migrations
    # stay unapplied (the api entrypoint runs `migrate` on start, but only sees the
    # migrations baked into the stale image). Layer cache makes an unchanged build
    # fast. `start`/`stop`/`up` stay pure bounces (no rebuild) for quick restarts.
    docker compose build $API_SERVICES frontend
    docker compose up -d
    sleep 5
    docker compose ps
    ;;
  rebuild)
    echo "Rebuilding and restarting NetPulse..."
    docker compose build ${2:-}
    docker compose up -d
    sleep 10
    docker compose ps
    ;;
  rebuild-api)
    # Each api-based service builds its OWN image (netpulse-<service>) from the
    # shared ./services/api context — they do NOT share one image — so build them
    # all (layer cache makes this fast) before recreating with --no-deps.
    echo "Rebuilding all api-based service images and restarting them..."
    docker compose build $API_SERVICES
    docker compose up -d --no-deps $API_SERVICES
    sleep 10
    docker compose ps $API_SERVICES
    ;;
  rebuild-frontend)
    echo "Rebuilding frontend image and restarting frontend..."
    docker compose build frontend
    docker compose up -d --no-deps frontend
    sleep 10
    docker compose ps frontend
    ;;
  fix-nat)
    # Re-apply the Docker MASQUERADE NAT rule (containers egress as the host IP).
    # Useful after a reboot if the rule wasn't persisted and SNMP/SSH from the
    # containers stops working. Idempotent.
    # shellcheck source=scripts/nat.sh
    . "$(dirname "$0")/scripts/nat.sh"
    apply_docker_nat
    ;;
  install-service)
    # Install + enable the systemd units (netpulse + netpulse-nat) for boot start.
    # shellcheck source=scripts/systemd.sh
    . "$(dirname "$0")/scripts/systemd.sh"
    install_systemd_service
    ;;
  uninstall-service)
    # shellcheck source=scripts/systemd.sh
    . "$(dirname "$0")/scripts/systemd.sh"
    uninstall_systemd_service
    ;;
  service-status)
    # shellcheck source=scripts/systemd.sh
    . "$(dirname "$0")/scripts/systemd.sh"
    systemd_service_status
    ;;
  status)
    docker compose ps
    echo ""
    echo "--- Health ---"
    curl -s http://localhost:8000/api/health/ | python3 -m json.tool
    echo ""
    echo "--- Infrastructure ---"
    curl -s http://localhost:8000/api/health/infrastructure/ | python3 -m json.tool
    ;;
  logs)
    docker compose logs -f ${2:-api}
    ;;
  health)
    # Full post-setup health verification against the running infrastructure.
    docker compose exec api python manage.py run_health_checks "${@:2}"
    ;;
  credentials)
    # Inspect credential profiles. Safe by default (lengths/status only).
    # Pass --show-secrets to reveal values, --profile-id N for one profile.
    docker compose exec api python manage.py show_credentials "${@:2}"
    ;;
  credentials-hint)
    # Show the initial admin login saved by setup.sh (~/netpulse-credentials.txt).
    CRED_FILE="$HOME/netpulse-credentials.txt"
    if [ -f "$CRED_FILE" ]; then
      echo "📋 NetPulse credentials ($CRED_FILE):"
      echo ""
      cat "$CRED_FILE"
    else
      echo "Credentials file not found ($CRED_FILE)."
      echo "Check your password manager, or reset the admin password with:"
      echo "  $0 reset-admin-password"
    fi
    ;;
  reset-admin-password)
    # Generate a new random password for the admin user and set it directly.
    # The password is delivered over STDIN (never argv / shell interpolation) so
    # any special characters survive intact and it isn't exposed in `ps` output.
    ADMIN_USER=$(grep -E '^DJANGO_SUPERUSER_USERNAME=' .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//;s/[[:space:]]*$//')
    ADMIN_USER=${ADMIN_USER:-admin}
    NEW_PASS=$(openssl rand -base64 16 | tr -d '/+=')
    if printf '%s' "$NEW_PASS" | docker compose exec -T -e NP_ADMIN_USER="$ADMIN_USER" api python manage.py shell -c '
import os, sys
from django.contrib.auth import get_user_model
User = get_user_model()
name = os.environ["NP_ADMIN_USER"]
try:
    u = User.objects.get(username=name)
except User.DoesNotExist:
    raise SystemExit("User %s not found" % name)
u.set_password(sys.stdin.read())
u.is_active = True
u.save()
print("Password reset successfully")
'; then
      echo ""
      echo "New password for '${ADMIN_USER}': ${NEW_PASS}"
      echo "Save this password securely!"
      # Keep the reference file in sync so credentials-hint stays accurate.
      CRED_FILE="$HOME/netpulse-credentials.txt"
      if [ -f "$CRED_FILE" ]; then
        TMP=$(mktemp)
        sed -e "s|^Password: .*|Password: ${NEW_PASS}|" -e "s|^Username: .*|Username: ${ADMIN_USER}|" "$CRED_FILE" > "$TMP" && mv "$TMP" "$CRED_FILE"
        chmod 600 "$CRED_FILE"
        echo "Updated ${CRED_FILE}"
      fi
    else
      echo "Failed to reset password (is the api container running? './netpulse.sh start')" >&2
      exit 1
    fi
    ;;
  install-watchdog)
    # Install the health watchdog as a cron job (every 5 min) + logrotate.
    WATCHDOG="$(pwd)/scripts/watchdog.sh"
    if [ ! -f "$WATCHDOG" ]; then
      echo "ERROR: $WATCHDOG not found" >&2; exit 1
    fi
    chmod +x "$WATCHDOG"
    echo "Installing spane watchdog..."
    # Log file with the invoking user as owner so cron writes without sudo.
    sudo touch /var/log/spane-watchdog.log
    sudo chown "$USER:$USER" /var/log/spane-watchdog.log
    sudo tee /etc/logrotate.d/spane-watchdog > /dev/null << 'LOGROTATE'
/var/log/spane-watchdog.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    dateext
}
LOGROTATE
    CRON_LINE="*/5 * * * * $WATCHDOG >> /var/log/spane-watchdog.log 2>&1"
    # Replace any existing watchdog cron entry, then add the current one.
    ( crontab -l 2>/dev/null | grep -v "watchdog.sh"; echo "$CRON_LINE" ) | crontab -
    echo "Watchdog installed (cron: */5 * * * *)."
    echo "  Log:    /var/log/spane-watchdog.log"
    echo "  Manual: $WATCHDOG"
    echo "  Status: ./netpulse.sh watchdog-status"
    echo "  Remove: ./netpulse.sh remove-watchdog"
    ;;
  remove-watchdog)
    crontab -l 2>/dev/null | grep -v "watchdog.sh" | crontab - 2>/dev/null || true
    sudo rm -f /etc/logrotate.d/spane-watchdog
    echo "Watchdog removed from cron."
    ;;
  watchdog-status)
    echo "=== Watchdog Status ==="
    if crontab -l 2>/dev/null | grep -q "watchdog.sh"; then
      echo "Cron: installed (every 5 min)"
    else
      echo "Cron: not installed — run: ./netpulse.sh install-watchdog"
    fi
    echo ""
    echo "=== Last 20 log entries ==="
    tail -20 /var/log/spane-watchdog.log 2>/dev/null || echo "No log file yet."
    ;;
  backup)
    echo "Running spane backup..."
    shift
    exec ./scripts/backup.sh "$@"
    ;;
  restore)
    shift
    if [ -z "${1:-}" ]; then
      echo "Usage: $0 restore <backup-file.[enc.]tar.gz>" >&2; exit 1
    fi
    exec ./scripts/restore.sh "$@"
    ;;
  list-backups)
    BACKUP_DIR="${BACKUP_LOCAL_PATH:-/opt/spane/backups}"
    echo "Backups in ${BACKUP_DIR}:"
    if [ -d "$BACKUP_DIR" ]; then
      ls -lh "$BACKUP_DIR"/spane-backup-*.tar.gz 2>/dev/null || echo "  (none)"
    else
      echo "  (directory does not exist)"
    fi
    ;;
  update)
    # Safe self-update: snapshot, .env back-fill, DB backup, migrate, rebuild,
    # health verify. Pass --yes to skip the confirmation prompt.
    bash "$(dirname "$0")/scripts/update.sh" "${2:-}"
    ;;
  show-version)
    if command -v git >/dev/null 2>&1 && git rev-parse --git-dir >/dev/null 2>&1; then
      VER_FILE="$(cat VERSION 2>/dev/null || echo '')"
      echo "spane version: ${VER_FILE:+$VER_FILE — }1.0.$(git rev-list --count HEAD) ($(git describe --tags --always 2>/dev/null || git rev-parse --short HEAD))"
    else
      echo "spane version: $(cat VERSION 2>/dev/null || echo unknown)"
    fi
    echo ""
    echo "Update history (last 10):"
    tail -n 10 .update-history.log 2>/dev/null || echo "  (no updates recorded yet)"
    ;;
  rollback)
    # Roll the working tree back to a pre-update snapshot tag, then rebuild.
    echo "Recent pre-update snapshots:"
    git tag 2>/dev/null | grep '^pre-update-' | sort -r | head -5 || true
    echo ""
    SNAP="${2:-}"
    if [ -z "$SNAP" ]; then
      read -r -p "Snapshot tag to restore (blank to abort): " SNAP
    fi
    [ -z "$SNAP" ] && { echo "Aborted."; exit 1; }
    if ! git rev-parse "$SNAP" >/dev/null 2>&1; then
      echo "❌ Unknown snapshot: $SNAP"; exit 1
    fi
    echo "⚠️  Rolling back to $SNAP and rebuilding. The DB is NOT downgraded —"
    echo "    restore a .update-db-backup-*.sql.gz manually if a migration must be reverted."
    read -r -p "Continue? [y/N]: " c; case "${c:-}" in y|Y) ;; *) echo "Aborted."; exit 1 ;; esac
    git checkout "$SNAP"
    GIT_COMMIT="$(git rev-parse --short HEAD)" GIT_COUNT="$(git rev-list --count HEAD)" \
      BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$0" rebuild-api
    "$0" rebuild-frontend
    echo "✅ Rolled back to $SNAP. You are in 'detached HEAD' — git checkout main when ready."
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|rebuild [service]|rebuild-api|rebuild-frontend|update|show-version|rollback|fix-nat|install-service|uninstall-service|service-status|status|health|credentials|credentials-hint|reset-admin-password|install-watchdog|remove-watchdog|watchdog-status|backup|restore <file>|list-backups|logs [service]}"
    echo ""
    echo "  start              Start all services"
    echo "  stop               Stop all services"
    echo "  restart            Stop and start all services"
    echo "  rebuild [service]  Rebuild image(s) and restart everything"
    echo "  rebuild-api        Rebuild the api image and recreate all api-based"
    echo "                     services (--no-deps; infra left running)"
    echo "  rebuild-frontend   Rebuild and recreate the frontend (--no-deps)"
    echo "  update [--yes]     Safe self-update (snapshot, .env back-fill, DB backup,"
    echo "                     migrate, rebuild, health verify; --yes skips the prompt)"
    echo "  show-version       Show the running version + recent update history"
    echo "  rollback [tag]     Roll back to a pre-update snapshot tag and rebuild"
    echo "  fix-nat            Re-apply the Docker NAT rule (run after a reboot if"
    echo "                     SNMP/SSH from containers stops working)"
    echo "  install-service    Install + enable the systemd service (start on boot)"
    echo "  uninstall-service  Disable + remove the systemd service"
    echo "  service-status     Show the systemd service status"
    echo "  status             Show service status and health"
    echo "  health             Run full post-setup health checks (add --json/--fail-fast)"
    echo "  credentials        Show credential profile status (add --show-secrets to reveal values)"
    echo "  credentials-hint   Show the initial admin login saved by setup.sh"
    echo "  reset-admin-password  Set a new random admin password and print it"
    echo "  install-watchdog   Install the health watchdog cron job (every 5 min)"
    echo "  remove-watchdog    Remove the watchdog cron job + logrotate config"
    echo "  watchdog-status    Show watchdog cron state + recent log entries"
    echo "  backup             Run a platform backup now (scripts/backup.sh)"
    echo "  restore <file>     Restore from a backup archive (scripts/restore.sh)"
    echo "  list-backups       List local backup archives"
    echo "  logs [service]     Follow logs (default: api)"
    exit 1
    ;;
esac
