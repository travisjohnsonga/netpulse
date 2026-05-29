#!/usr/bin/env bash
# Compile proto definitions for local development.
# Run from anywhere; script resolves paths automatically.
# Requires: pip install grpcio-tools
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

mkdir -p "$ROOT/proto_generated"

python -m grpc_tools.protoc \
    --proto_path="$ROOT/proto" \
    --python_out="$ROOT/proto_generated" \
    --grpc_python_out="$ROOT/proto_generated" \
    gnmi.proto \
    gnmi_ext.proto

touch "$ROOT/proto_generated/__init__.py"

echo "Compiled → $ROOT/proto_generated/"
