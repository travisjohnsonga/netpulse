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
    rhel|centos|fedora|rocky|almalinux)
        PKG_MGR="dnf"
        log "Detected: $PRETTY_NAME"
        ;;
    *)
        warn "Unsupported OS: $OS"
        warn "This installer is tested on Ubuntu 22.04/24.04"
        read -p "Continue anyway? [y/N]: " yn
        [[ "$yn" =~ ^[Yy]$ ]] || exit 1
        PKG_MGR="apt-get"
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
    read -p "Continue as root? [y/N]: " yn
    [[ "$yn" =~ ^[Yy]$ ]] || exit 1
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
    sudo apt-get install -y -qq \
        curl wget git jq \
        ca-certificates gnupg \
        lsb-release apt-transport-https \
        iptables-persistent netfilter-persistent \
        net-tools
    log "Prerequisites installed"
}

install_dnf() {
    sudo dnf install -y -q \
        curl wget git jq \
        ca-certificates gnupg \
        iptables-services \
        net-tools
    log "Prerequisites installed"
}

case "$PKG_MGR" in
    apt-get) install_apt ;;
    dnf)     install_dnf ;;
esac

# ─── Install Docker ────────────────────────
section "Installing Docker"

if command -v docker &>/dev/null; then
    DOCKER_VERSION=$(docker --version | cut -d' ' -f3 | tr -d ',')
    log "Docker already installed: $DOCKER_VERSION"
else
    log "Installing Docker..."

    case "$OS" in
        ubuntu|debian)
            curl -fsSL https://get.docker.com | sudo sh
            ;;
        rhel|centos|rocky|almalinux|fedora)
            sudo dnf install -y docker-ce docker-ce-cli \
                containerd.io docker-buildx-plugin \
                docker-compose-plugin
            ;;
        *)
            curl -fsSL https://get.docker.com | sudo sh
            ;;
    esac

    log "Docker installed"
fi

# Add user to docker group
if ! groups "$USER" | grep -q docker; then
    sudo usermod -aG docker "$USER"
    log "Added $USER to docker group"
    warn "You may need to log out and back in for"
    warn "docker group membership to take effect."
    warn "The installer will use 'sudo docker' for now."
    DOCKER_CMD="sudo docker"
else
    DOCKER_CMD="docker"
fi

# Start and enable Docker
sudo systemctl enable docker --now
log "Docker service enabled and started"

# Verify Docker
$DOCKER_CMD --version || error "Docker installation failed"

# ─── Install Docker Compose ────────────────
section "Installing Docker Compose"

if docker compose version &>/dev/null 2>&1 || \
   sudo docker compose version &>/dev/null 2>&1; then
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
    read -p "Update existing installation? [y/N]: " yn
    if [[ "$yn" =~ ^[Yy]$ ]]; then
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
