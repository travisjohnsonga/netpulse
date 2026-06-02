#!/usr/bin/env bash
#
# Run the NetPulse API integration test suite inside the running `api`
# container. These tests exercise the REAL DRF REST API against the app's own
# in-memory SQLite test DB (config.settings.test) — NOT live network devices.
#
# Usage:
#   ./scripts/run_integration_tests.sh            # full integration suite
#   ./scripts/run_integration_tests.sh --no-devices  # skip requires_devices
#   ./scripts/run_integration_tests.sh --with-devices # run requires_devices too
#
# Run from the repo root (where docker-compose.yml lives).
set -euo pipefail

# Locate repo root: this script lives at services/api/scripts/.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
cd "${REPO_ROOT}"

green() { printf '\033[0;32m%s\033[0m\n' "$1"; }
yellow() { printf '\033[0;33m%s\033[0m\n' "$1"; }
red() { printf '\033[0;31m%s\033[0m\n' "$1"; }

# 1. Confirm the api service is up.
if ! docker compose ps --status running api 2>/dev/null | grep -q api; then
  red "The 'api' service is not running. Start it with: docker compose up -d api"
  exit 1
fi
green "api service is running."

# 2. Sync the integration tests (and pytest.ini) into the container — the image
#    bakes code in, so host edits are not visible until copied.
yellow "Copying integration tests into the container..."
docker compose cp services/api/tests/integration api:/app/tests/integration
docker compose cp services/api/pytest.ini api:/app/pytest.ini

# 3. Run them.
PYTEST_ARGS="tests/integration -q"
case "${1:-}" in
  --with-devices)
    yellow "Running integration tests WITH requires_devices (NETPULSE_DEVICE_TESTS=1)..."
    docker compose exec -e NETPULSE_DEVICE_TESTS=1 api python -m pytest ${PYTEST_ARGS}
    ;;
  --no-devices|"")
    yellow "Running integration tests (requires_devices skipped)..."
    docker compose exec api python -m pytest ${PYTEST_ARGS}
    ;;
  *)
    red "Unknown option: $1"
    echo "Usage: $0 [--no-devices|--with-devices]"
    exit 2
    ;;
esac

green "Integration tests complete."
