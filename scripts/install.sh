#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────
#  NetPulse One-Line Installer
#  https://github.com/travisjohnsonga/netpulse
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/travisjohnsonga/netpulse/main/scripts/install.sh | bash
#    curl -fsSL .../install.sh | bash -s -- --dir /opt/netpulse
#    NETPULSE_DIR=/opt/netpulse curl -fsSL .../install.sh | bash
# ─────────────────────────────────────────

# Never let apt/dpkg pop an interactive ncurses dialog. When this script is run
# via `curl … | bash`, stdin is the curl pipe (not a TTY), so any package that
# prompts (iptables-persistent, etc.) would hang forever waiting for input.
export DEBIAN_FRONTEND=noninteractive

NETPULSE_REPO="https://github.com/travisjohnsonga/netpulse.git"
NETPULSE_DIR="${NETPULSE_DIR:-$HOME/netpulse}"
NETPULSE_BRANCH="${NETPULSE_BRANCH:-main}"

# Parse optional flags (e.g. --dir /opt/netpulse, --branch dev).
while [ $# -gt 0 ]; do
    case "$1" in
        --dir)    NETPULSE_DIR="$2"; shift 2 ;;
        --branch) NETPULSE_BRANCH="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()     { echo -e "${GREEN}[netpulse]${NC} $*"; }
warn()    { echo -e "${YELLOW}[warning]${NC} $*"; }
error()   { echo -e "${RED}[error]${NC} $*"; exit 1; }
section() { echo -e "\n${BLUE}━━━ $* ━━━${NC}"; }

# Yes/no prompt that is safe under `curl … | bash`. When stdin is not a TTY
# (piped install) we cannot read an answer, so we auto-apply the supplied
# default and warn instead of blocking on `read`.
#   confirm "Question? [y/N]:" <default Y|N>  → exit 0 for yes, 1 for no
confirm() {
    local prompt="$1" default="${2:-N}" yn
    if [ ! -t 0 ]; then
        warn "Non-interactive mode — assuming '$default' for: $prompt"
        yn="$default"
    else
        read -p "$prompt " yn || yn="$default"
        yn="${yn:-$default}"
    fi
    [[ "$yn" =~ ^[Yy]$ ]]
}

# ─── Banner ───────────────────────────────
cat << 'BANNER'

  ███╗   ██╗███████╗████████╗██████╗ ██╗   ██╗██╗     ███████╗███████╗
  ████╗  ██║██╔════╝╚══██╔══╝██╔══██╗██║   ██║██║     ██╔════╝██╔════╝
  ██╔██╗ ██║█████╗     ██║   ██████╔╝██║   ██║██║     ███████╗█████╗
  ██║╚██╗██║██╔══╝     ██║   ██╔═══╝ ██║   ██║██║     ╚════██║██╔══╝
  ██║ ╚████║███████╗   ██║   ██║     ╚██████╔╝███████╗███████║███████╗
  ╚═╝  ╚═══╝╚══════╝   ╚═╝   ╚═╝      ╚═════╝ ╚══════╝╚══════╝╚══════╝

  Network Intelligence Platform
  https://github.com/travisjohnsonga/netpulse

BANNER

# ─── Check OS ─────────────────────────────
section "Checking system"

OS=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
fi

case "$OS" in
    ubuntu|debian)
        PKG_MGR="apt-get"
        log "Detected: $PRETTY_NAME"
        ;;
    *)
        error "Unsupported OS: ${OS:-unknown}
       NetPulse supports Ubuntu 22.04/24.04.
       Please use a supported OS."
        ;;
esac

# Check architecture
ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64) ;;
    aarch64|arm64) warn "ARM64 detected - Docker images may be slower" ;;
    *) error "Unsupported architecture: $ARCH" ;;
esac

# Check we're not root
if [ "$EUID" -eq 0 ]; then
    warn "Running as root is not recommended."
    warn "Consider running as a regular user with sudo access."
    # Non-interactive default = Y: `curl … | sudo bash` is a common install path
    # and root already has every privilege the installer needs.
    confirm "Continue as root? [y/N]:" Y || exit 1
fi

# Check sudo
if ! sudo -n true 2>/dev/null; then
    log "sudo access required - you may be prompted for password"
    sudo -v || error "sudo access required"
fi

# ─── Install prerequisites ─────────────────
section "Installing prerequisites"

install_apt() {
    sudo apt-get update -qq

    # iptables-persistent normally pops an ncurses dialog asking whether to save
    # the current IPv4/IPv6 rules. Pre-seed the answers via debconf so the
    # install runs unattended (critical for `curl … | bash` where stdin is the
    # pipe, not a terminal). We let it save existing IPv4 rules but not IPv6;
    # NetPulse re-applies its own MASQUERADE NAT via scripts/nat.sh afterwards.
    echo iptables-persistent iptables-persistent/autosave_v4 boolean true \
        | sudo debconf-set-selections
    echo iptables-persistent iptables-persistent/autosave_v6 boolean false \
        | sudo debconf-set-selections

    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
        curl wget git jq \
        ca-certificates gnupg \
        lsb-release apt-transport-https \
        iptables-persistent netfilter-persistent \
        net-tools
    log "Prerequisites installed"
}

install_apt

# ─── Install Docker ────────────────────────
section "Installing Docker"

if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | tr -d ',')
    log "Docker already installed: $DOCKER_VERSION"
else
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | sudo DEBIAN_FRONTEND=noninteractive sh
    log "Docker installed"
fi

# Add user to docker group
if ! groups "$USER" | grep -q docker; then
    sudo usermod -aG docker "$USER"
    log "Added $USER to docker group"
fi

# Start and enable Docker
sudo systemctl enable docker --now
log "Docker service enabled and started"

# Resolve how we talk to the Docker daemon for the rest of THIS session.
#
# `usermod -aG docker` only takes effect in a NEW login session — the current
# shell still lacks the group, so a bare `docker ps` hits the socket with the
# old (groupless) credentials and fails with "permission denied". Rather than
# guess from group membership, probe the daemon directly: try unprivileged
# first, fall back to sudo. setup.sh inherits these via the environment.
USED_SUDO_DOCKER=0
if docker ps &>/dev/null; then
    DOCKER_CMD="docker"
    COMPOSE_CMD="docker compose"
elif sudo docker ps &>/dev/null; then
    log "Docker group not active in this session yet — using 'sudo docker' for now"
    DOCKER_CMD="sudo docker"
    COMPOSE_CMD="sudo docker compose"
    USED_SUDO_DOCKER=1
else
    error "Docker is not accessible (daemon not running or permission denied)"
fi
export DOCKER_CMD COMPOSE_CMD

# Verify Docker
$DOCKER_CMD --version || error "Docker installation failed"

# ─── Install Docker Compose ────────────────
section "Installing Docker Compose"

if $COMPOSE_CMD version &>/dev/null 2>&1; then
    log "Docker Compose already installed"
else
    log "Installing Docker Compose plugin..."
    COMPOSE_VERSION=$(curl -fsSL \
        https://api.github.com/repos/docker/compose/releases/latest \
        | grep '"tag_name"' | cut -d'"' -f4)
    sudo mkdir -p /usr/local/lib/docker/cli-plugins
    sudo curl -fsSL \
        "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
    sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    log "Docker Compose $COMPOSE_VERSION installed"
fi

# ─── Clone NetPulse ────────────────────────
section "Cloning NetPulse"

if [ -d "$NETPULSE_DIR/.git" ]; then
    warn "Directory $NETPULSE_DIR already exists"
    if confirm "Update existing installation? [y/N]:" N; then
        cd "$NETPULSE_DIR"
        git pull origin "$NETPULSE_BRANCH"
        log "Updated to latest version"
    else
        log "Using existing installation"
        cd "$NETPULSE_DIR"
    fi
else
    log "Cloning to $NETPULSE_DIR..."
    git clone --branch "$NETPULSE_BRANCH" \
        "$NETPULSE_REPO" "$NETPULSE_DIR"
    cd "$NETPULSE_DIR"
    log "Cloned successfully"
fi

# ─── Run setup ─────────────────────────────
section "Running NetPulse setup"

chmod +x netpulse.sh scripts/*.sh

log "Starting setup..."
./scripts/setup.sh

# ─── Done ─────────────────────────────────
section "Installation complete"

cat << EOF

${GREEN}✅ NetPulse installed successfully!${NC}

  Directory:  $NETPULSE_DIR
  Dashboard:  https://$(hostname -I | awk '{print $1}')
  Docs:       https://github.com/travisjohnsonga/netpulse

  Manage:
    cd $NETPULSE_DIR
    ./netpulse.sh status
    ./netpulse.sh health

  Auto-start on boot:
    ./netpulse.sh install-service

EOF

if [ "$USED_SUDO_DOCKER" -eq 1 ]; then
    echo ""
    echo -e "${YELLOW}⚠️  NOTE: Docker group membership was just added.${NC}"
    echo "   Log out and back in (or run 'newgrp docker') so you can run"
    echo "   docker commands without sudo:"
    echo "     cd $NETPULSE_DIR && ./netpulse.sh status"
    echo ""
fi
