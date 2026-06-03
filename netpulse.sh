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
  *)
    echo "Usage: $0 {start|stop|restart|rebuild [service]|rebuild-api|rebuild-frontend|status|health|logs [service]}"
    echo ""
    echo "  start              Start all services"
    echo "  stop               Stop all services"
    echo "  restart            Stop and start all services"
    echo "  rebuild [service]  Rebuild image(s) and restart everything"
    echo "  rebuild-api        Rebuild the api image and recreate all api-based"
    echo "                     services (--no-deps; infra left running)"
    echo "  rebuild-frontend   Rebuild and recreate the frontend (--no-deps)"
    echo "  status             Show service status and health"
    echo "  health             Run full post-setup health checks (add --json/--fail-fast)"
    echo "  logs [service]     Follow logs (default: api)"
    exit 1
    ;;
esac

token() {
    curl -s -X POST http://localhost:8000/api/auth/token/ \
      -H 'Content-Type: application/json' \
      -d '{"username":"admin","password":"netmagic"}' | \
      python3 -m json.tool | grep '"access"' | cut -d'"' -f4
}
