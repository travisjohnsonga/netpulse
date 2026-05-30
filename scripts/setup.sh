#!/usr/bin/env bash
#
# NetPulse first-run setup.
#
# Interactive configurator: copies .env.example → .env, prompts for the values
# that must change (admin + infrastructure credentials, collector IP, optional
# integrations), generates strong random secrets for anything you skip, runs a
# few pre-flight safety checks, and can start the stack for you.
#
# Idempotent: re-run any time to update individual values — existing answers in
# .env are shown as the defaults in [brackets]. Never commits .env.
#
set -euo pipefail

# ── paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
EXAMPLE="$ROOT_DIR/.env.example"
ENV_FILE="$ROOT_DIR/.env"

# ── colors ────────────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  R=$'\e[31m'; G=$'\e[32m'; Y=$'\e[33m'; B=$'\e[34m'; BOLD=$'\e[1m'; N=$'\e[0m'
else
  R=''; G=''; Y=''; B=''; BOLD=''; N=''
fi
ok()   { echo "${G}✅ $*${N}"; }
warn() { echo "${Y}⚠️  $*${N}"; }
err()  { echo "${R}❌ $*${N}" >&2; }
info() { echo "${B}→${N} $*"; }

# ── helpers ───────────────────────────────────────────────────────────────────
gen_secret() { openssl rand -base64 36 2>/dev/null | tr -d '/+=' | cut -c1-32; }

# current value of KEY in the working .env (empty if unset)
env_get() {
  local key="$1"
  [ -f "$ENV_FILE" ] || { echo ""; return; }
  sed -n "s/^${key}=//p" "$ENV_FILE" | head -n1
}

# set KEY=VALUE in .env (replace existing line or append). Value written verbatim.
env_set() {
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  local found=0
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" == "${key}="* ]]; then
      printf '%s=%s\n' "$key" "$val" >> "$tmp"; found=1
    else
      printf '%s\n' "$line" >> "$tmp"
    fi
  done < "$ENV_FILE"
  [ "$found" -eq 1 ] || printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$ENV_FILE"
}

# prompt KEY "Question" [default] — plain text input
ask() {
  local key="$1" prompt="$2" def="${3:-$(env_get "$key")}" reply
  read -r -p "$(printf '%s [%s]: ' "$prompt" "${def}")" reply || true
  env_set "$key" "${reply:-$def}"
}

# prompt_secret KEY "Question" [minlen] — hidden input; blank → keep/generate
ask_secret() {
  local key="$1" prompt="$2" minlen="${3:-0}" cur reply confirm
  cur="$(env_get "$key")"
  local hint="leave blank to keep existing"
  case "$cur" in ""|change-me|change-me-in-production|change-me-to-a-random-50-char-string) hint="leave blank to auto-generate";; esac
  while true; do
    read -r -s -p "$(printf '%s (%s): ' "$prompt" "$hint")" reply || true; echo
    if [ -z "$reply" ]; then
      case "$cur" in ""|change-me*|change-me-in-production)
        reply="$(gen_secret)"; env_set "$key" "$reply"; ok "generated a random value"; return;;
      *) info "keeping existing value"; return;; esac
    fi
    if [ "${#reply}" -lt "$minlen" ]; then err "must be at least $minlen characters"; continue; fi
    read -r -s -p "  confirm: " confirm || true; echo
    if [ "$reply" != "$confirm" ]; then err "values did not match — try again"; continue; fi
    env_set "$key" "$reply"; ok "set"; return
  done
}

yesno() { # yesno "Question" default(Y/n) → returns 0 for yes
  local prompt="$1" def="${2:-Y}" reply
  read -r -p "$(printf '%s (%s): ' "$prompt" "$([ "$def" = Y ] && echo 'Y/n' || echo 'y/N')")" reply || true
  reply="${reply:-$def}"
  [[ "$reply" =~ ^[Yy] ]]
}

# ── banner ────────────────────────────────────────────────────────────────────
echo "${BOLD}"
echo "╔════════════════════════════════════╗"
echo "║      NetPulse First-Run Setup      ║"
echo "╚════════════════════════════════════╝"
echo "${N}"
echo "Configures NetPulse for first deployment. Press Enter to accept the"
echo "value shown in [brackets]. Secrets left blank are auto-generated."
echo

# ── pre-flight checks ─────────────────────────────────────────────────────────
echo "${BOLD}Pre-flight checks${N}"
[ "$(id -u)" -eq 0 ] && warn "running as root — prefer a non-root user in the docker group"
command -v docker >/dev/null 2>&1 && ok "docker found" || err "docker not found — install Docker before continuing"
if docker compose version >/dev/null 2>&1; then ok "docker compose found"; else err "docker compose v2 not found"; fi
command -v openssl >/dev/null 2>&1 || warn "openssl not found — secret generation will fail"
for p in 80 443 "${FRONTEND_PORT:-3000}" "${API_PORT:-8000}"; do
  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${p} "; then
    warn "port ${p} is already in use"
  fi
done
if command -v free >/dev/null 2>&1; then
  mem_gb=$(free -g | awk '/^Mem:/{print $2}')
  [ "${mem_gb:-0}" -lt 4 ] && warn "less than 4GB RAM detected (${mem_gb}GB) — 4GB+ recommended" || ok "memory ok (${mem_gb}GB)"
fi
disk_gb=$(df -BG --output=avail "$ROOT_DIR" 2>/dev/null | tail -1 | tr -dc '0-9')
[ -n "${disk_gb:-}" ] && { [ "$disk_gb" -lt 20 ] && warn "less than 20GB free disk (${disk_gb}GB)" || ok "disk ok (${disk_gb}GB free)"; }
echo

# ── seed .env ─────────────────────────────────────────────────────────────────
[ -f "$EXAMPLE" ] || { err ".env.example not found at $EXAMPLE"; exit 1; }
if [ -f "$ENV_FILE" ]; then
  warn ".env already exists — re-running in update mode (existing values are the defaults)"
else
  cp "$EXAMPLE" "$ENV_FILE"; ok "created .env from .env.example"
fi
echo

# ── 1. basic config ───────────────────────────────────────────────────────────
echo "${BOLD}1. Basic configuration${N}"
detected_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
ask DJANGO_ALLOWED_HOSTS "Platform hostname / allowed hosts (comma-separated)" "$(env_get DJANGO_ALLOWED_HOSTS)"
ask COLLECTOR_IP         "Collector IP (devices send telemetry here)" "$(env_get COLLECTOR_IP || true)"
[ -z "$(env_get COLLECTOR_IP)" ] && [ -n "$detected_ip" ] && { env_set COLLECTOR_IP "$detected_ip"; info "defaulted collector IP to detected $detected_ip"; }
# Point the browser-facing URLs at the collector IP for non-localhost installs.
ci="$(env_get COLLECTOR_IP)"
if [ -n "$ci" ] && [ "$ci" != "127.0.0.1" ]; then
  env_set REACT_APP_API_URL "http://${ci}:$(env_get API_PORT || echo 8000)"
  env_set REACT_APP_WS_URL  "ws://${ci}:$(env_get API_PORT || echo 8000)"
fi
echo

# ── 2. credentials ────────────────────────────────────────────────────────────
echo "${BOLD}2. Credentials${N}  ${Y}(blank = auto-generate a strong secret)${N}"
ask        DJANGO_SUPERUSER_USERNAME "Admin username" "$(env_get DJANGO_SUPERUSER_USERNAME)"
ask        DJANGO_SUPERUSER_EMAIL    "Admin email"    "$(env_get DJANGO_SUPERUSER_EMAIL)"
ask_secret DJANGO_SUPERUSER_PASSWORD "Admin password" 12
# Django secret key is never user-facing — always strong & random if unset.
case "$(env_get DJANGO_SECRET_KEY)" in ""|change-me*) env_set DJANGO_SECRET_KEY "$(gen_secret)$(gen_secret)"; ok "generated Django secret key";; esac
ask_secret POSTGRES_PASSWORD         "PostgreSQL password"      8
ask_secret INFLUXDB_ADMIN_PASSWORD   "InfluxDB admin password"  8
case "$(env_get INFLUXDB_ADMIN_TOKEN)" in ""|change-me*) env_set INFLUXDB_ADMIN_TOKEN "$(gen_secret)$(gen_secret)"; ok "generated InfluxDB admin token";; esac
ask_secret OPENSEARCH_PASSWORD       "OpenSearch admin password" 8
ask_secret NATS_PASSWORD             "NATS password"            8
ask_secret VALKEY_PASSWORD           "Valkey password"          8
# OpenBao is auto-initialised/unsealed; leave OPENBAO_TOKEN blank (file-managed).
env_set OPENBAO_TOKEN ""
echo

# ── 3. optional integrations ──────────────────────────────────────────────────
echo "${BOLD}3. Optional integrations${N}"
if yesno "Configure NVD API key for CVE data?" n; then
  info "Sign up: https://nvd.nist.gov/developers/request-an-api-key"
  ask NVD_API_KEY "NVD API key" "$(env_get NVD_API_KEY)"
fi
if yesno "Configure Cisco PSIRT (openVuln) API?" n; then
  info "Register: https://apiconsole.cisco.com/"
  ask CISCO_PSIRT_CLIENT_ID     "Cisco PSIRT client ID"     "$(env_get CISCO_PSIRT_CLIENT_ID)"
  ask CISCO_PSIRT_CLIENT_SECRET "Cisco PSIRT client secret" "$(env_get CISCO_PSIRT_CLIENT_SECRET)"
fi
if yesno "Configure SMTP for email alerts?" n; then
  ask SMTP_HOST     "SMTP host" "$(env_get SMTP_HOST)"
  ask SMTP_PORT     "SMTP port" "$(env_get SMTP_PORT)"
  ask SMTP_USER     "SMTP username" "$(env_get SMTP_USER)"
  ask_secret SMTP_PASSWORD "SMTP password" 0
fi
echo

# ── 4. summary ────────────────────────────────────────────────────────────────
masked() { local v; v="$(env_get "$1")"; [ -n "$v" ] && echo "configured" || echo "not set"; }
echo "${BOLD}Configuration summary${N}"
echo "┌──────────────────────────────────────────────┐"
printf "│ %-16s %-27s │\n" "Allowed hosts:" "$(env_get DJANGO_ALLOWED_HOSTS)"
printf "│ %-16s %-27s │\n" "Collector IP:"  "$(env_get COLLECTOR_IP)"
printf "│ %-16s %-27s │\n" "Admin user:"    "$(env_get DJANGO_SUPERUSER_USERNAME)"
printf "│ %-16s %-27s │\n" "Admin pass:"    "$(masked DJANGO_SUPERUSER_PASSWORD)"
printf "│ %-16s %-27s │\n" "Postgres pass:" "$(masked POSTGRES_PASSWORD)"
printf "│ %-16s %-27s │\n" "NVD API:"       "$(masked NVD_API_KEY)"
printf "│ %-16s %-27s │\n" "Cisco PSIRT:"   "$(masked CISCO_PSIRT_CLIENT_ID)"
printf "│ %-16s %-27s │\n" "SMTP:"          "$(masked SMTP_HOST)"
echo "└──────────────────────────────────────────────┘"

# Warn about any remaining placeholder defaults.
if grep -q "change-me" "$ENV_FILE"; then
  warn "some values still contain 'change-me' — review $ENV_FILE before production"
fi
echo
ok ".env written to $ENV_FILE"
echo

# ── 5. start ──────────────────────────────────────────────────────────────────
url_host="$(env_get COLLECTOR_IP)"; [ -z "$url_host" ] && url_host="localhost"
if yesno "Pull and start NetPulse now?" Y; then
  info "pulling images (this can take a while)…"
  (cd "$ROOT_DIR" && docker compose pull || warn "some images could not be pulled (will build on up)")
  info "starting the stack…"
  (cd "$ROOT_DIR" && docker compose up -d)
  echo
  ok "NetPulse is starting!"
  echo "   Web UI:   http://${url_host}:$(env_get FRONTEND_PORT || echo 3000)"
  echo "   API docs: http://${url_host}:$(env_get API_PORT || echo 8000)/api/docs/"
else
  info "skipped startup. When ready:  docker compose up -d"
  echo "   Web UI will be at http://${url_host}:$(env_get FRONTEND_PORT || echo 3000)"
fi
