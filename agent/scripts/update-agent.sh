#!/usr/bin/env bash
#
# update-agent.sh — update the NetPulse agent on this Linux host.
#
# Modes (most operators need NO arguments — see the third form):
#   1. Download from a server:   sudo ./update-agent.sh --server https://<server> [--insecure]
#   2. Swap from a local binary: sudo ./update-agent.sh --binary /path/to/netpulse-agent-linux-amd64
#   3. No args (the common case): sudo ./update-agent.sh
#      → reads server_url (and insecure_tls) from the enrolled config.json, so a
#        host updates "from wherever it enrolled" with nothing to type.
#
# Arg style + paths mirror install.sh (--server / --insecure, /usr/local/bin +
# /etc/netpulse-agent), and the download path is the same the installer uses
# ({server}/agent/download/linux-${ARCH}).
#
# Safety features (the whole point — keep these intact):
#   - Verifies the NEW binary runs + reports a version BEFORE replacing the
#     running one (catches a stale/wrong/corrupt download — the "source was
#     actually old" trap).
#   - Backs up the current binary so a bad update can be rolled back.
#   - Confirms the service comes back up AND reports the new version after
#     restart; auto-rolls-back if the new binary fails to start.
#   - Each step is checked (no && chain hiding a mid-sequence failure).
#
set -euo pipefail

SERVICE="netpulse-agent"
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/netpulse-agent"
BIN_PATH="${INSTALL_DIR}/${SERVICE}"
BACKUP_PATH="${BIN_PATH}.bak"

SERVER_URL=""
LOCAL_BINARY=""
INSECURE=0
INSECURE_SET=0
CONFIG_PATH="${CONFIG_DIR}/config.json"

# ---- args ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    --server)   SERVER_URL="$2"; shift 2 ;;
    --binary)   LOCAL_BINARY="$2"; shift 2 ;;
    --config)   CONFIG_PATH="$2"; shift 2 ;;
    --insecure) INSECURE=1; INSECURE_SET=1; shift ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -28
      exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ "$EUID" -ne 0 ]]; then
  echo "ERROR: must run as root (sudo) — it stops/starts the service and writes ${INSTALL_DIR}." >&2
  exit 1
fi

# Read a string value for a JSON key from config.json without requiring jq
# (the agent ships no extra tooling). Good enough for the flat top-level keys we
# need (server_url). Prints the value or nothing.
read_config_str() {
  local key="$1"
  [[ -f "$CONFIG_PATH" ]] || return 0
  grep -oE "\"${key}\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$CONFIG_PATH" \
    | head -1 | sed -E "s/.*:[[:space:]]*\"([^\"]*)\".*/\1/"
}
read_config_bool_true() {  # echoes 1 if the JSON bool key is true
  local key="$1"
  [[ -f "$CONFIG_PATH" ]] || return 0
  grep -qE "\"${key}\"[[:space:]]*:[[:space:]]*true" "$CONFIG_PATH" && echo 1 || true
}

# ---- default the server (and self-signed flag) from the enrolled config ----
# "update from wherever I'm enrolled" — so a local run needs no arguments.
if [[ -z "$SERVER_URL" && -z "$LOCAL_BINARY" ]]; then
  SERVER_URL="$(read_config_str server_url)"
  if [[ -n "$SERVER_URL" ]]; then
    echo "No --server given; using server_url from ${CONFIG_PATH}: ${SERVER_URL}"
    if [[ "$INSECURE_SET" -eq 0 && "$(read_config_bool_true insecure_tls)" == "1" ]]; then
      INSECURE=1
      echo "  (config has insecure_tls=true → downloading with --insecure)"
    fi
  fi
fi

if [[ -z "$SERVER_URL" && -z "$LOCAL_BINARY" ]]; then
  echo "ERROR: no --server, no --binary, and no server_url in ${CONFIG_PATH}." >&2
  echo "  sudo $0                       # update from the enrolled server" >&2
  echo "  sudo $0 --server https://host [--insecure]" >&2
  echo "  sudo $0 --binary /path/to/netpulse-agent-linux-amd64" >&2
  exit 1
fi

# ---- arch ----
case "$(uname -m)" in
  x86_64)  ARCH="amd64" ;;
  aarch64) ARCH="arm64" ;;
  *) echo "ERROR: unsupported arch $(uname -m)" >&2; exit 1 ;;
esac

# Extract the agent's version from `<bin> --version`. The agent logs a
# timestamped line to STDERR ("<ts> netpulse-agent vX.Y.Z"), so match the
# "netpulse-agent vX.Y.Z" token specifically (robust to the timestamp prefix +
# stderr framing) rather than blindly taking the last field. Echoes the version,
# "(unreadable)" if it ran but printed no recognizable version, or "(none)" if
# the binary didn't run.
agent_version() {
  local bin="$1" out ver
  out="$("$bin" --version 2>&1)" || { echo "(none)"; return; }
  ver="$(printf '%s\n' "$out" \
    | grep -oE 'netpulse-agent[[:space:]]+v?[0-9]+\.[0-9]+\.[0-9]+[^[:space:]]*' \
    | head -1 | awk '{print $NF}')"
  [[ -n "$ver" ]] && echo "$ver" || echo "(unreadable)"
}

# ---- current version (for before/after comparison) ----
CURRENT_VER="(none installed)"
if [[ -x "$BIN_PATH" ]]; then
  CURRENT_VER="$(agent_version "$BIN_PATH")"
fi
echo "Current installed version: ${CURRENT_VER}"

# ---- obtain the new binary into a temp file ----
TMP_BIN="$(mktemp /tmp/netpulse-agent.XXXXXX)"
cleanup() { rm -f "$TMP_BIN"; }
trap cleanup EXIT

if [[ -n "$LOCAL_BINARY" ]]; then
  echo "Using local binary: ${LOCAL_BINARY}"
  [[ -f "$LOCAL_BINARY" ]] || { echo "ERROR: $LOCAL_BINARY not found." >&2; exit 1; }
  cp "$LOCAL_BINARY" "$TMP_BIN"
else
  DL_URL="${SERVER_URL%/}/agent/download/linux-${ARCH}"
  echo "Downloading: ${DL_URL}"
  # -f fail on HTTP error (so a 404 page isn't saved as the "binary"),
  # -L follow redirects (download → GitHub release), -k for a self-signed cert.
  CURL_OPTS=(-fL -o "$TMP_BIN")
  [[ "$INSECURE" -eq 1 ]] && CURL_OPTS+=(-k)
  curl "${CURL_OPTS[@]}" "$DL_URL"
fi
chmod +x "$TMP_BIN"

# ---- VERIFY the new binary BEFORE touching the running one ----
NEW_VER="$(agent_version "$TMP_BIN")"
if [[ "$NEW_VER" == "(none)" || "$NEW_VER" == "(unreadable)" ]]; then
  echo "ERROR: the downloaded/provided binary won't run or reports no version. Aborting — running agent untouched." >&2
  exit 1
fi
echo "New binary version:        ${NEW_VER}"

if [[ "$NEW_VER" == "$CURRENT_VER" ]]; then
  echo "NOTE: new version (${NEW_VER}) == current (${CURRENT_VER}). Re-applying anyway."
fi

# ---- stop, back up, swap ----
echo "Stopping ${SERVICE}..."
systemctl stop "$SERVICE" 2>/dev/null || echo "  (service was not running)"

if [[ -x "$BIN_PATH" ]]; then
  echo "Backing up current binary -> ${BACKUP_PATH}"
  cp "$BIN_PATH" "$BACKUP_PATH"
fi

echo "Installing new binary -> ${BIN_PATH}"
cp "$TMP_BIN" "$BIN_PATH"
chmod +x "$BIN_PATH"

# ---- start + confirm ----
echo "Starting ${SERVICE}..."
systemctl start "$SERVICE"
sleep 2

if systemctl is-active --quiet "$SERVICE"; then
  RUNNING_VER="$(agent_version "$BIN_PATH")"
  echo ""
  echo "✅ Update complete. ${SERVICE} is active, reporting version: ${RUNNING_VER}"
  echo "   (was ${CURRENT_VER} → now ${RUNNING_VER})"
  [[ -f "$BACKUP_PATH" ]] && echo "   Previous binary backed up at ${BACKUP_PATH} (remove when satisfied)."
else
  echo ""
  echo "❌ ${SERVICE} did NOT come back up after the swap. Rolling back..." >&2
  if [[ -f "$BACKUP_PATH" ]]; then
    cp "$BACKUP_PATH" "$BIN_PATH"
    chmod +x "$BIN_PATH"
    systemctl start "$SERVICE" || true
    echo "   Rolled back to the previous binary (${CURRENT_VER}). Investigate before retrying." >&2
  else
    echo "   No backup to roll back to. Check: systemctl status ${SERVICE} -l" >&2
  fi
  exit 1
fi
