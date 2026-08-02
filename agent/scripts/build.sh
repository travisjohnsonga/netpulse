#!/usr/bin/env bash
# Cross-compile the NetPulse Agent for all supported platforms.
set -euo pipefail
cd "$(dirname "$0")/.."

# Scope to the AGENT's own tag prefix (vX.Y.Z) so it never picks up the app's
# app-v* tags — the app and agent version independently in one repo. (--match
# 'v[0-9]*' = plain v-number tags only, e.g. v1.5.0, not app-v0.5.0.)
VERSION="$(git describe --tags --match 'v[0-9]*' --always 2>/dev/null || echo dev)"
echo "Building NetPulse Agent ${VERSION}..."

# Resolve the windows-only deps (x/sys, wmi) so the windows build links cleanly.
go mod tidy

mkdir -p dist
LDFLAGS="-s -w -X main.Version=${VERSION}"

build() {
  local goos="$1" goarch="$2" ext="${3:-}"
  echo "  -> ${goos}/${goarch}"
  GOOS="$goos" GOARCH="$goarch" CGO_ENABLED=0 \
    go build -ldflags "$LDFLAGS" \
    -o "dist/netpulse-agent-${goos}-${goarch}${ext}" ./cmd/netpulse-agent/
}

build linux   amd64
build linux   arm64
build windows amd64 .exe

echo "Build complete:"
ls -lh dist/
