#!/usr/bin/env bash
# Fix the OpenBao token in .env after a factory reset.
#
# Normally OPENBAO_TOKEN is left blank and services read the root token from the
# shared keys file (/openbao/data/.init_keys). This script copies that token
# into .env for the rare case where an explicit OPENBAO_TOKEN is wanted.
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT_TOKEN=$(docker compose exec -T openbao sh -c 'cat /openbao/data/.init_keys 2>/dev/null' \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('root_token',''))" 2>/dev/null || true)

if [ -z "${ROOT_TOKEN}" ]; then
  echo "ERROR: could not read the OpenBao root token from /openbao/data/.init_keys."
  echo "  Is OpenBao initialised? Try: docker compose exec api python manage.py init_openbao"
  exit 1
fi

if grep -q '^OPENBAO_TOKEN=' .env; then
  sed -i "s|^OPENBAO_TOKEN=.*|OPENBAO_TOKEN=${ROOT_TOKEN}|" .env
else
  printf '\nOPENBAO_TOKEN=%s\n' "${ROOT_TOKEN}" >> .env
fi

echo "✅ OpenBao token written to .env"
echo "Restart services to pick it up: docker compose restart api ingest-snmp scheduler config-manager"
