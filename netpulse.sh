#!/bin/bash
# NetPulse management script

cd "$(dirname "$0")"

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
  *)
    echo "Usage: $0 {start|stop|restart|rebuild [service]|status|logs [service]}"
    echo ""
    echo "  start              Start all services"
    echo "  stop               Stop all services"
    echo "  restart            Stop and start all services"
    echo "  rebuild [service]  Rebuild image(s) and restart"
    echo "  status             Show service status and health"
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

# Rebuild and restart all api-based services
rebuild-api() {
    echo "Rebuilding api image and restarting all api-based services..."
    docker compose build api
    docker compose up -d \
        api websocket config-manager scheduler \
        alert-engine cve-engine lifecycle-engine \
        security-engine stream-processor check-engine
    echo "Done. Waiting for health..."
    sleep 15
    docker compose ps | grep -E "api|engine|processor|manager|scheduler|websocket" | \
        grep -v "ingest" | awk '{print $1, $NF}'
}
