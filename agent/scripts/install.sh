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
SERVICE_FILE="/etc/systemd/system/netpulse-agent.service"

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
curl "${CURL_OPTS[@]}" -o "${INSTALL_DIR}/netpulse-agent" "${SERVER_URL}/agent/download/linux-${ARCH}"
chmod +x "${INSTALL_DIR}/netpulse-agent"

# Dedicated low-privilege user.
id -u netpulse-agent &>/dev/null || \
  useradd -r -s /sbin/nologin -d /nonexistent netpulse-agent

mkdir -p "$CONFIG_DIR"
"${INSTALL_DIR}/netpulse-agent" \
  --enroll "$TOKEN" --server "$SERVER_URL" --config "${CONFIG_DIR}/config.json" \
  "${ENROLL_OPTS[@]}"
chown -R netpulse-agent:netpulse-agent "$CONFIG_DIR"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=NetPulse Monitoring Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${INSTALL_DIR}/netpulse-agent --config ${CONFIG_DIR}/config.json
Restart=always
RestartSec=30
User=netpulse-agent
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${CONFIG_DIR}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now netpulse-agent
echo "✅ NetPulse Agent installed. Status: systemctl status netpulse-agent"
