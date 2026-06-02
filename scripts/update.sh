#!/usr/bin/env bash
#
# NetPulse self-update: pull the latest code from origin/main, rebuild only the
# services that changed, apply migrations (the api entrypoint also runs them),
# and report the new version.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=================================================="
echo "  NetPulse Update"
echo "=================================================="
echo ""

CURRENT="$(git rev-parse --short HEAD)"
CURRENT_COUNT="$(git rev-list --count HEAD)"
echo "Current version: 1.0.${CURRENT_COUNT} (${CURRENT})"

echo "Checking for updates..."
git fetch origin main --quiet

LATEST="$(git rev-parse --short origin/main)"
LATEST_COUNT="$(git rev-list --count origin/main)"

if [ "$CURRENT" = "$LATEST" ]; then
  echo "✅ Already up to date (1.0.${LATEST_COUNT})."
  exit 0
fi

BEHIND="$(git rev-list --count HEAD..origin/main)"
echo "📦 Update available: 1.0.${LATEST_COUNT}  (${BEHIND} commit(s) behind origin/main)"
echo ""
echo "Changes:"
git log --oneline "HEAD..origin/main" | head -20
echo ""

read -r -p "Apply update? [y/N]: " confirm
case "${confirm:-}" in
  y|Y) ;;
  *) echo "Update cancelled."; exit 0 ;;
esac

# Which top-level areas changed (decide what to rebuild).
CHANGED="$(git diff --name-only "HEAD..origin/main")"

echo ""
echo "1/4 Pulling latest code..."
git pull --ff-only origin main

echo "2/4 Rebuilding API services (migrations run on api startup)..."
./netpulse.sh rebuild-api

if echo "$CHANGED" | grep -q "^services/frontend/"; then
  echo "3/4 Rebuilding frontend (frontend files changed)..."
  ./netpulse.sh rebuild-frontend
else
  echo "3/4 Frontend unchanged — skipping rebuild."
fi

echo "4/4 Verifying services..."
UP="$(docker compose ps --status running --format '{{.Service}}' | wc -l | tr -d ' ')"
echo "    services running: ${UP}"

NEW_COUNT="$(git rev-list --count HEAD)"
NEW_COMMIT="$(git rev-parse --short HEAD)"
echo ""
echo "=================================================="
echo "  ✅ Update complete — version 1.0.${NEW_COUNT} (${NEW_COMMIT})"
echo "=================================================="
