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
    docker compose down
    ;;
  restart)
    echo "Restarting NetPulse..."
    docker compose down
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
  *)
    echo "Usage: $0 {start|stop|restart|rebuild [service]|rebuild-api|rebuild-frontend|fix-nat|install-service|uninstall-service|service-status|status|health|credentials|credentials-hint|reset-admin-password|logs [service]}"
    echo ""
    echo "  start              Start all services"
    echo "  stop               Stop all services"
    echo "  restart            Stop and start all services"
    echo "  rebuild [service]  Rebuild image(s) and restart everything"
    echo "  rebuild-api        Rebuild the api image and recreate all api-based"
    echo "                     services (--no-deps; infra left running)"
    echo "  rebuild-frontend   Rebuild and recreate the frontend (--no-deps)"
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
    echo "  logs [service]     Follow logs (default: api)"
    exit 1
    ;;
esac
