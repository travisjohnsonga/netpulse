#!/usr/bin/env bash
# NetPulse Agent installer (Linux). Downloads the binary, enrolls, and installs
# a hardened systemd unit. Always use the https:// server URL — nginx redirects
# http→https, and a redirected POST would drop the enrollment body. Add
# --insecure for a self-signed server cert (skips TLS verification). Usage:
#   curl -fsSL https://<server>/agent/install | sudo bash -s -- \
#     --server https://<server> --token <TOKEN> [--insecure]
set -euo pipefail

INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/netpulse-agent"

SERVER_URL=""
TOKEN=""
INSECURE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server)   SERVER_URL="$2"; shift 2 ;;
    --token)    TOKEN="$2";      shift 2 ;;
    --insecure) INSECURE="1";    shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [ -z "$SERVER_URL" ] || [ -z "$TOKEN" ]; then
  echo "Usage: install.sh --server URL --token TOKEN [--insecure]" >&2
  exit 1
fi

# For a self-signed server: skip cert verification on both the binary download
# (curl -k) and enrollment (agent --insecure).
CURL_OPTS=(-fsSL)
ENROLL_OPTS=()
if [ -n "$INSECURE" ]; then
  CURL_OPTS+=(-k)
  ENROLL_OPTS+=(--insecure)
fi

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)  ARCH="amd64" ;;
  aarch64) ARCH="arm64" ;;
  *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac

echo "Installing NetPulse Agent (linux-${ARCH})..."

# Handle re-runs / upgrades cleanly. Stop a running agent and remove the old
# binary before downloading (a running binary can be busy/locked). Works for
# both fresh installs (no-ops) and upgrades.
if systemctl is-active --quiet netpulse-agent 2>/dev/null; then
  echo "Stopping existing agent..."
  systemctl stop netpulse-agent
fi
rm -f "${INSTALL_DIR}/netpulse-agent"

curl "${CURL_OPTS[@]}" -o "${INSTALL_DIR}/netpulse-agent" "${SERVER_URL}/agent/download/linux-${ARCH}"
chmod +x "${INSTALL_DIR}/netpulse-agent"

# Dedicated low-privilege user.
id -u netpulse-agent &>/dev/null || \
  useradd -r -s /sbin/nologin -d /nonexistent netpulse-agent

# Log forwarding tails the curated security logs (/var/log/auth.log,
# /var/log/syslog), which are mode 640 owned by syslog:adm — so the agent user
# must be in a group that can READ them or the tailer silently reads NOTHING
# (the original log-forwarding "2 docs ever" bug). adm covers Debian/Ubuntu's
# /var/log; systemd-journal covers journald-only distros. Idempotent on re-run.
usermod -aG adm netpulse-agent 2>/dev/null || true
getent group systemd-journal >/dev/null 2>&1 && \
  usermod -aG systemd-journal netpulse-agent 2>/dev/null || true

CONFIG_PATH="${CONFIG_DIR}/config.json"
mkdir -p "$CONFIG_DIR"
# Enroll, capturing output so we can give a clear message if the host already
# has an enrolled agent. Re-running normally succeeds — the server re-enrolls in
# place (rotating the cert) — but a conflicting record returns HTTP 409.
set +e
ENROLL_OUT="$("${INSTALL_DIR}/netpulse-agent" \
  --enroll "$TOKEN" --server "$SERVER_URL" --config "$CONFIG_PATH" \
  "${ENROLL_OPTS[@]}" 2>&1)"
ENROLL_RC=$?
set -e
printf '%s\n' "$ENROLL_OUT"
if [ "$ENROLL_RC" -ne 0 ]; then
  if printf '%s' "$ENROLL_OUT" | grep -q "HTTP 409"; then
    echo "⚠️  This host already has an enrolled agent." >&2
    echo "   Revoke it in NetPulse: Settings → Agents → $(hostname) → Revoke," >&2
    echo "   then re-run this installer." >&2
  fi
  echo "❌ Enrollment failed." >&2
  exit 1
fi
chown -R netpulse-agent:netpulse-agent "$CONFIG_DIR"

# Enrollment succeeded — install the hardened systemd unit (writes the unit,
# daemon-reload, enable + start). The unit runs as the netpulse-agent user.
"${INSTALL_DIR}/netpulse-agent" \
  --install-service \
  --config "$CONFIG_PATH"

# Leave a persistent updater so the host can be updated later with a single
# no-arg command (it reads server_url from config.json). SECURITY: it lands in
# ${INSTALL_DIR} (root-owned, root-write-only) — NOT /tmp — because it runs
# elevated and swaps the agent binary; a user-writable copy would be a privesc
# vector. Best-effort: a fetch failure doesn't fail the install.
UPDATE_PATH="${INSTALL_DIR}/netpulse-agent-update.sh"
if curl "${CURL_OPTS[@]}" -o "$UPDATE_PATH" "${SERVER_URL}/agent/update"; then
  chmod 0755 "$UPDATE_PATH"
  echo "   Update later with: sudo ${UPDATE_PATH}"
else
  rm -f "$UPDATE_PATH"
  echo "   (could not fetch the updater; re-pull later from ${SERVER_URL}/agent/update)" >&2
fi

echo "✅ NetPulse Agent installed. Status: systemctl status netpulse-agent"
