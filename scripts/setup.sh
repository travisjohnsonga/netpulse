#!/usr/bin/env bash
#
# spane first-run setup.
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

# Shared Docker MASQUERADE NAT helper (apply_docker_nat / detect_docker_subnet).
# shellcheck source=scripts/nat.sh
. "$SCRIPT_DIR/nat.sh"

# Shared systemd-service helpers (install_systemd_service / …).
# shellcheck source=scripts/systemd.sh
. "$SCRIPT_DIR/systemd.sh"

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

# Detect a real terminal on stdin. When this script is reached through the
# one-line installer (`curl … | bash` → install.sh → setup.sh), stdin is the
# curl pipe, so every `read` gets EOF. In that case we fall back to defaults
# (auto-generated secrets, dev ports) instead of prompting, and tell the user
# how to re-run interactively to customise anything.
if [ -t 0 ]; then INTERACTIVE=1; else INTERACTIVE=0; fi
if [ "$INTERACTIVE" -eq 0 ]; then
  warn "Non-interactive mode (no TTY on stdin) — using safe defaults:"
  warn "  • all infrastructure + admin secrets auto-generated"
  warn "  • standard web ports (80/443)"
  warn "  • optional integrations skipped"
  warn "Re-run interactively to customise:  cd \"$ROOT_DIR\" && ./scripts/setup.sh"
  echo
fi

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
  if [ "${INTERACTIVE:-1}" -eq 0 ]; then
    reply="$def"   # no TTY: take the default, don't block on read
  else
    read -r -p "$(printf '%s (%s): ' "$prompt" "$([ "$def" = Y ] && echo 'Y/n' || echo 'y/N')")" reply || true
    reply="${reply:-$def}"
  fi
  [[ "$reply" =~ ^[Yy] ]]
}

# Strip an inline "# comment" and surrounding whitespace from an .env value so a
# commented value (e.g. "10.0.0.1  # collector") never leaks into the URL display.
_clean_env_value() { printf '%s' "$1" | sed 's/[[:space:]]*#.*$//' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'; }

# Best-effort detection of the host's primary IP. Prefer the source address the
# kernel would use to reach the internet (skips docker bridge IPs like 172.18.x);
# fall back to the first address from `hostname -I`.
_detect_host_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'src \K\S+' | head -1)"
  [ -z "$ip" ] && ip="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' | head -1)"
  printf '%s' "$ip"
}

# Report the admin account's password state on the running stack. Echoes:
#   default  – admin exists and still has the forced-change default password
#   custom   – admin exists and has already changed its password
#   missing  – admin row not found (stack not seeded yet)
#   unknown  – could not query (stack not running)
# ensure_superuser seeds the admin with the fixed default + must_change_password
# on first start; we never reset an existing/changed password from here.
admin_password_state() {
  local user out
  user="$(_clean_env_value "$(env_get DJANGO_SUPERUSER_USERNAME)")"; [ -z "$user" ] && user="admin"
  out="$($COMPOSE exec -T -e NP_ADMIN_USER="$user" api python manage.py shell -c '
import os
from django.contrib.auth import get_user_model
u = get_user_model().objects.filter(username=os.environ["NP_ADMIN_USER"]).first()
print("default" if (u and u.must_change_password) else ("custom" if u else "missing"))
' 2>/dev/null | tr -d '\r' | grep -E '^(default|custom|missing)$' | tail -1)"
  printf '%s' "${out:-unknown}"
}

# Ensure DJANGO_ALLOWED_HOSTS lets the dashboard be reached by IP. Django (in
# production settings) rejects any Host not in this list, so a fresh install
# with only localhost blocks browser access by IP. Merge localhost + 127.0.0.1 +
# the configured collector IP + every address from `hostname -I` into the
# existing value, de-duplicated and order-preserving.
merge_allowed_hosts() {
  local candidates ip final=""
  candidates="localhost 127.0.0.1"
  candidates="$candidates $(_clean_env_value "$(env_get DJANGO_ALLOWED_HOSTS)" | tr ',' ' ')"
  candidates="$candidates $(hostname -I 2>/dev/null)"
  candidates="$candidates $(_clean_env_value "$(env_get COLLECTOR_IP)")"
  for ip in $candidates; do
    [ -z "$ip" ] && continue
    case ",$final," in *",$ip,"*) continue;; esac   # already present → skip
    final="${final:+$final,}$ip"
  done
  env_set DJANGO_ALLOWED_HOSTS "$final"
  info "allowed hosts: $final"
}

# Post-start sanity check: confirm the running api would accept this server's IP.
verify_allowed_hosts() {
  local ip ah
  ip="$(_detect_host_ip)"
  [ -z "$ip" ] && return 0
  ah="$($COMPOSE exec -T api python -c 'from django.conf import settings; print(",".join(settings.ALLOWED_HOSTS))' 2>/dev/null | tr -d '\r')"
  case ",$ah," in
    *",*,"*|*",$ip,"*) ok "ALLOWED_HOSTS includes this server ($ip)";;
    *) warn "ALLOWED_HOSTS may not include this server's IP ($ip) — browser access by IP could be blocked."
       warn "  current: ${ah:-<empty>}"
       warn "  fix: add '$ip' to DJANGO_ALLOWED_HOSTS in .env, then ./netpulse.sh restart";;
  esac
}

# ── infrastructure secrets ──────────────────────────────────────────────────────
# Secrets that can be batch auto-generated with gen_secret(). Order is cosmetic.
INFRA_SECRETS=(
  POSTGRES_PASSWORD
  NATS_PASSWORD
  INFLUXDB_ADMIN_PASSWORD
  VALKEY_PASSWORD
  OPENSEARCH_PASSWORD
  INFLUXDB_ADMIN_TOKEN
  DJANGO_SECRET_KEY
)

# Keys/tokens want more entropy than passwords → two concatenated secrets.
_is_long_secret() { case "$1" in DJANGO_SECRET_KEY|INFLUXDB_ADMIN_TOKEN) return 0;; *) return 1;; esac; }

# gen_for KEY → echo a freshly generated secret of the right length for KEY.
gen_for() {
  if _is_long_secret "$1"; then printf '%s%s' "$(gen_secret)" "$(gen_secret)"; else gen_secret; fi
}

# True if VALUE is empty or a shipped placeholder (i.e. still needs a real value).
_is_placeholder() {
  case "$1" in ""|change-me|change-me-in-production|change-me-to-a-random-50-char-string) return 0;; *) return 1;; esac
}

# generate_infra_secrets MODE   MODE=fill → only placeholders; rotate → all.
generate_infra_secrets() {
  local mode="${1:-fill}" key cur n=0
  for key in "${INFRA_SECRETS[@]}"; do
    cur="$(env_get "$key")"
    if [ "$mode" = rotate ] || _is_placeholder "$cur"; then
      env_set "$key" "$(gen_for "$key")"; n=$((n + 1))
    fi
  done
  ok "generated $n infrastructure secret(s)"
}

# prompt_secret KEY "Label" — hidden prompt; blank → auto-generate (or keep).
prompt_secret() {
  local key="$1" label="$2" cur reply def="auto-generate"
  cur="$(env_get "$key")"
  _is_placeholder "$cur" || def="keep existing"
  read -r -s -p "$(printf '  → %s [%s]: ' "$label" "$def")" reply || true; echo
  if [ -n "$reply" ]; then
    env_set "$key" "$reply"; ok "$key set"
  elif [ "$def" = "auto-generate" ]; then
    env_set "$key" "$(gen_for "$key")"; ok "$key auto-generated"
  else
    info "$key kept"
  fi
}

# ── argument handling ───────────────────────────────────────────────────────────
usage() {
  cat <<EOF
${BOLD}spane first-run setup${N}

Usage: scripts/setup.sh [OPTIONS]

Options:
  --generate-secrets   Rotate ALL infrastructure secrets in an existing .env
                       and exit. Does NOT touch admin credentials or re-key
                       already-initialised data volumes (see the warning it
                       prints). Safe on a fresh, never-started stack.
  -h, --help           Show this help and exit.

With no options, runs the interactive first-run configurator: copies
.env.example → .env, prompts for the values that must change, auto-generates
strong secrets for anything left blank, and can start the stack.
EOF
}

# --generate-secrets: rotate infra secrets in place, then exit.
rotate_secrets() {
  if [ ! -f "$ENV_FILE" ]; then
    err "--generate-secrets needs an existing .env (run setup first)"; exit 1
  fi
  echo "${BOLD}Rotate infrastructure secrets${N}"
  warn "This regenerates these .env values: ${INFRA_SECRETS[*]}"
  warn "It updates .env ONLY. It does NOT re-key data already written by"
  warn "Postgres / InfluxDB / OpenSearch — those store their own copy of the"
  warn "password internally. On a stack that has ALREADY started, the new .env"
  warn "passwords will NOT match the data volumes and services will fail to"
  warn "authenticate until you change each engine's internal password too (or"
  warn "wipe its volume). This is safe only on a fresh, never-started stack."
  echo
  if ! yesno "Rotate anyway?" n; then info "aborted — no changes made"; exit 0; fi
  generate_infra_secrets rotate
  ok "infrastructure secrets rotated in $ENV_FILE"
  warn "secrets are stored in $ENV_FILE — keep it secure and never commit it"
  exit 0
}

case "${1:-}" in
  -h|--help)          usage; exit 0;;
  --generate-secrets) rotate_secrets;;
  "")                 ;;  # interactive (default)
  *)                  err "unknown option: $1"; echo; usage; exit 1;;
esac

# ── banner ────────────────────────────────────────────────────────────────────
echo "${BOLD}"
echo "╔════════════════════════════════════╗"
echo "║        spane First-Run Setup       ║"
echo "╚════════════════════════════════════╝"
echo "${N}"
echo "Configures spane for first deployment. Press Enter to accept the"
echo "value shown in [brackets]. Secrets left blank are auto-generated."
echo

# ── pre-flight checks ─────────────────────────────────────────────────────────
echo "${BOLD}Pre-flight checks${N}"
[ "$(id -u)" -eq 0 ] && warn "running as root — prefer a non-root user in the docker group"
command -v docker >/dev/null 2>&1 && ok "docker found" || err "docker not found — install Docker before continuing"

# Resolve how to invoke docker compose. The installer exports COMPOSE_CMD when a
# freshly-added docker group isn't active in the session yet (so it must use
# sudo). When run standalone, detect it ourselves: probe daemon-socket access
# (`docker ps`) — not just `docker compose version`, which works without the
# socket and so would mask a permission problem.
if [ -n "${COMPOSE_CMD:-}" ]; then
  COMPOSE="$COMPOSE_CMD"
elif docker ps >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif sudo docker ps >/dev/null 2>&1; then
  COMPOSE="sudo docker compose"
  warn "docker group not active in this session — using 'sudo docker compose'"
else
  err "cannot access the Docker daemon (not running or permission denied)"; exit 1
fi
if $COMPOSE version >/dev/null 2>&1; then ok "docker compose found"; else err "docker compose v2 not found"; exit 1; fi
command -v openssl >/dev/null 2>&1 || warn "openssl not found — secret generation will fail"
for p in 80 443 "${FRONTEND_PORT:-80}" "${API_PORT:-8000}"; do
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

# Record the HOST's IP for the containers. Detection inside a container returns
# the container/bridge IP (172.x), so we capture the real host IP here (on the
# host, before the stack starts); register_local_collector + config generation
# use it so devices are pointed at the host, not a container.
host_ip="$(_detect_host_ip)"
if [ -n "$host_ip" ]; then
  env_set NETPULSE_HOST_IP "$host_ip"
  info "host IP for collectors: $host_ip"
fi

# Internal DNS so containers resolve on-prem hostnames (devices, NetBox, SMTP,
# UniFi). Wired into outbound services via docker-compose dns:/dns_search:.
# Auto-detected here; never overwrite an existing value. Skip the systemd-resolved
# stub (127.0.0.53) — it's not reachable from inside a container.
if [ -z "$(env_get INTERNAL_DNS)" ]; then
  internal_dns="$(resolvectl status 2>/dev/null | grep 'DNS Servers' | awk '{print $3}' | head -1)"
  [ -z "$internal_dns" ] && internal_dns="$(awk '/^nameserver/{print $2; exit}' /etc/resolv.conf 2>/dev/null)"
  if [ -n "$internal_dns" ] && [ "$internal_dns" != "127.0.0.53" ]; then
    env_set INTERNAL_DNS "$internal_dns"
    info "detected internal DNS server: $internal_dns"
  fi
fi
# Validate INTERNAL_DNS (auto-detected OR hand-edited): an invalid value makes
# docker-compose reject the stack with "invalid DNS address" at container start.
# Clear anything that isn't a plausible IPv4/IPv6 address so we fall back to the
# public resolver (8.8.8.8) instead of failing to boot.
_dns="$(env_get INTERNAL_DNS)"
if [ -n "$_dns" ]; then
  if echo "$_dns" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || echo "$_dns" | grep -q ':'; then
    : # looks like an IPv4 or IPv6 address — keep it
  else
    warn "INTERNAL_DNS '$_dns' is not a valid IP address — clearing it (will use 8.8.8.8)"
    env_set INTERNAL_DNS ""
  fi
fi
if [ -z "$(env_get INTERNAL_DOMAIN)" ]; then
  # "DNS Domain: foo.local bar.local" — take the first as INTERNAL_DOMAIN and an
  # optional second as INTERNAL_DOMAIN2 (second dns_search entry).
  domain_line="$(resolvectl status 2>/dev/null | grep 'DNS Domain' | head -1)"
  internal_domain="$(echo "$domain_line" | awk '{print $3}')"
  internal_domain2="$(echo "$domain_line" | awk '{print $4}')"
  if [ -n "$internal_domain" ]; then
    env_set INTERNAL_DOMAIN "$internal_domain"
    info "detected internal DNS domain: $internal_domain"
  fi
  if [ -n "$internal_domain2" ] && [ -z "$(env_get INTERNAL_DOMAIN2)" ]; then
    env_set INTERNAL_DOMAIN2 "$internal_domain2"
    info "detected second internal DNS domain: $internal_domain2"
  fi
fi

# Make sure the dashboard is reachable by IP (Django rejects unlisted Hosts).
# Done before the stack starts so the api reads the final value on first boot.
merge_allowed_hosts

# Web UI ports: production (80/443) is the default; dev (3000/3443) or custom.
echo "Web UI port configuration:"
echo "  1) Production  — 80 (HTTP) / 443 (HTTPS)  [default]"
echo "  2) Development — 3000 (HTTP) / 3443 (HTTPS)"
echo "  3) Custom"
printf "Choose [1]: "; read -r port_choice || true
case "${port_choice:-1}" in
  2) env_set FRONTEND_PORT 3000; env_set FRONTEND_HTTPS_PORT 3443 ;;
  3) ask FRONTEND_PORT       "HTTP port"  "$(env_get FRONTEND_PORT || echo 80)"
     ask FRONTEND_HTTPS_PORT "HTTPS port" "$(env_get FRONTEND_HTTPS_PORT || echo 443)" ;;
  # Production is the default: force the standard ports even when re-running over
  # an existing .env that still has the old dev ports (3000/3443).
  *) env_set FRONTEND_PORT 80; env_set FRONTEND_HTTPS_PORT 443 ;;
esac
ok "Web UI: HTTP $(env_get FRONTEND_PORT) (redirects to HTTPS) / HTTPS $(env_get FRONTEND_HTTPS_PORT)"
echo

# ── 2. credentials ────────────────────────────────────────────────────────────
# The initial admin is seeded with a FIXED default password and flagged
# must_change_password — the UI forces a change on first login. We don't prompt
# for (or generate) a password here; the operator sets their own after logging in.
DEFAULT_ADMIN_PASSWORD="spane1!"
echo "${BOLD}2. Admin credentials${N}"
ask DJANGO_SUPERUSER_USERNAME "Admin username" "$(env_get DJANGO_SUPERUSER_USERNAME)"
ask DJANGO_SUPERUSER_EMAIL    "Admin email"    "$(env_get DJANGO_SUPERUSER_EMAIL)"
env_set DJANGO_SUPERUSER_PASSWORD "$DEFAULT_ADMIN_PASSWORD"
info "admin starts with the default password '${DEFAULT_ADMIN_PASSWORD}' — you'll be required to change it on first login"
echo

# ── 2b. infrastructure secrets ──────────────────────────────────────────────────
# Postgres/NATS/InfluxDB/Valkey/OpenSearch passwords + the Django secret key and
# InfluxDB token. These are never user-facing — auto-generating is recommended.
echo "${BOLD}3. Infrastructure secrets${N}  ${Y}(Postgres, NATS, InfluxDB, Valkey, OpenSearch, Django key)${N}"
if yesno "Auto-generate all infrastructure secrets?" Y; then
  generate_infra_secrets fill
else
  info "enter each secret, or press Enter to auto-generate / keep it"
  prompt_secret POSTGRES_PASSWORD       "PostgreSQL password"
  prompt_secret NATS_PASSWORD           "NATS password"
  prompt_secret INFLUXDB_ADMIN_PASSWORD "InfluxDB admin password"
  prompt_secret VALKEY_PASSWORD         "Valkey password"
  prompt_secret OPENSEARCH_PASSWORD     "OpenSearch admin password"
  prompt_secret INFLUXDB_ADMIN_TOKEN    "InfluxDB admin token"
  prompt_secret DJANGO_SECRET_KEY       "Django secret key"
fi
# OpenBao is auto-initialised/unsealed on first start; leave OPENBAO_TOKEN blank.
env_set OPENBAO_TOKEN ""
echo

# ── 4. optional integrations ──────────────────────────────────────────────────
echo "${BOLD}4. Optional integrations${N}"
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

# ── 5. summary ────────────────────────────────────────────────────────────────
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

# Make sure .env can't be committed (whole-line match so ".env.example" doesn't count).
if [ -f "$ROOT_DIR/.gitignore" ] && grep -qx ".env" "$ROOT_DIR/.gitignore"; then
  ok ".env is gitignored"
else
  warn ".env is NOT in .gitignore — add a line containing exactly '.env' to avoid committing secrets"
fi
echo
ok ".env written to $ENV_FILE"
warn "secrets are stored in $ENV_FILE — keep it secure and never commit it"
echo

# Mark setup as complete so the UI stops showing the first-run /setup page.
if grep -q "SETUP_COMPLETE" "$ENV_FILE" 2>/dev/null; then
  sed -i 's/SETUP_COMPLETE=false/SETUP_COMPLETE=true/' "$ENV_FILE"
else
  echo "SETUP_COMPLETE=true" >> "$ENV_FILE"
fi
ok "SETUP_COMPLETE=true set in .env"
echo

# ── 6. start ──────────────────────────────────────────────────────────────────
url_host="$(_clean_env_value "$(env_get COLLECTOR_IP)")"
[ -z "$url_host" ] && url_host="$(_detect_host_ip)"
[ -z "$url_host" ] && url_host="localhost"
if yesno "Pull and start spane now?" Y; then
  info "pulling images (this can take a while)…"
  (cd "$ROOT_DIR" && $COMPOSE pull || warn "some images could not be pulled (will build on up)")
  info "starting the stack…"
  (cd "$ROOT_DIR" && $COMPOSE up -d)
  echo
  # Always NAT container traffic to the host IP — devices that filter SNMP/SSH by
  # source IP see the host, and the 172.x bridge can't collide with the network.
  echo "📡 Configuring container networking…"
  echo "   NAT rule ensures devices see the host IP for SNMP and SSH connections."
  if apply_docker_nat; then
    ok "Network NAT configured"
  else
    warn "Could not apply Docker NAT rule — run: sudo ./netpulse.sh fix-nat"
  fi
  echo
  info "Downloading SNMP MIB files (LibreNMS + net-snmp + Cisco)…"
  (cd "$ROOT_DIR" && ./scripts/download_mibs.sh) \
    || warn "MIB download failed — run ./scripts/download_mibs.sh later"
  echo
  ok "spane is starting!"
  echo "   Web UI:   https://${url_host}:$(env_get FRONTEND_HTTPS_PORT || echo 443)  (HTTP :$(env_get FRONTEND_PORT || echo 80) redirects here)"
  echo "   API docs: http://${url_host}:$(env_get API_PORT || echo 8000)/api/docs/"
  echo
  info "Waiting for services to come up, then running health checks…"
  sleep 30
  if (cd "$ROOT_DIR" && $COMPOSE exec -T api python manage.py run_health_checks); then
    ok "All health checks passed."
  else
    warn "Some health checks failed (see report above). Re-run later with: ./netpulse.sh health"
  fi
  echo
  # Confirm the running api will accept browser requests to this server's IP.
  verify_allowed_hosts
  echo
  if yesno "Install spane as a systemd service to start on boot?" N; then
    install_systemd_service
  else
    info "skipped — install later with: ./netpulse.sh install-service"
  fi
  echo
  # Health watchdog: cron job that every 5 min restarts unhealthy containers,
  # recovers the api (incl. unsealing OpenBao), and guards against fd leaks.
  if yesno "Install the health watchdog? (recommended — auto-recovers failed services)" Y; then
    (cd "$ROOT_DIR" && ./netpulse.sh install-watchdog) \
      || warn "watchdog install failed — run later with: ./netpulse.sh install-watchdog"
  else
    info "skipped — install later with: ./netpulse.sh install-watchdog"
  fi
else
  info "skipped startup. When ready:  $COMPOSE up -d"
  echo "   Web UI will be at https://${url_host}:$(env_get FRONTEND_HTTPS_PORT || echo 443)"
fi

# ── 7. credentials ────────────────────────────────────────────────────────────
# The admin password is frequently auto-generated (the user left it blank), so
# it would otherwise be unknown. Show it prominently and save a 0600 reference
# file — the api container creates the superuser from these .env values on start.
show_credentials() {
  local host user pass https_port url cred_file state
  host="$(_clean_env_value "$(env_get COLLECTOR_IP)")"
  [ -z "$host" ] && host="$(_detect_host_ip)"
  [ -z "$host" ] && host="localhost"
  user="$(_clean_env_value "$(env_get DJANGO_SUPERUSER_USERNAME)")"; [ -z "$user" ] && user="admin"
  pass="$(env_get DJANGO_SUPERUSER_PASSWORD)"   # the fixed default (spane1!)
  https_port="$(_clean_env_value "$(env_get FRONTEND_HTTPS_PORT)")"; [ -z "$https_port" ] && https_port="443"
  if [ "$https_port" = "443" ]; then url="https://${host}"; else url="https://${host}:${https_port}"; fi

  # If this admin already changed its password (re-install over a persisted DB),
  # don't print the default — it would be wrong.
  state="$(admin_password_state)"
  if [ "$state" = "custom" ]; then
    echo
    echo "${BOLD}╔══════════════════════════════════════════════════════╗${N}"
    echo "${BOLD}║              NETPULSE LOGIN                          ║${N}"
    echo "${BOLD}╚══════════════════════════════════════════════════════╝${N}"
    echo "   URL:      ${url}"
    echo "   Username: ${user}"
    ok "This admin already has a custom password (left unchanged)."
    echo "   Forgot it?  ./netpulse.sh reset-admin-password"
    echo
    return
  fi

  echo
  echo "${BOLD}╔══════════════════════════════════════════════════════╗${N}"
  echo "${BOLD}║              NETPULSE LOGIN CREDENTIALS              ║${N}"
  echo "${BOLD}╚══════════════════════════════════════════════════════╝${N}"
  echo "   URL:      ${url}"
  echo "   Username: ${user}"
  echo "   Password: ${BOLD}${pass}${N}"
  echo "${BOLD}   ⚠️  You will be required to change this password on first login.${N}"
  echo

  cred_file="$HOME/netpulse-credentials.txt"
  cat > "$cred_file" <<EOF
spane Initial Credentials
Generated: $(date)
URL: ${url}
Username: ${user}
Password: ${pass}

You will be required to change this password on first login.
Delete this file after your first login.
EOF
  chmod 600 "$cred_file"
  ok "Credentials also saved to: ${cred_file}"
  echo "   View them again later with:  ./netpulse.sh credentials-hint"
  echo
}
show_credentials
