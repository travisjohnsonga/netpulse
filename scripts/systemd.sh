#!/usr/bin/env bash
#
# Shared systemd-service helpers for NetPulse.
#
# Source this file to get install_systemd_service / uninstall_systemd_service /
# systemd_service_status; it does nothing on its own. Used by scripts/setup.sh
# and netpulse.sh (install-service / uninstall-service / service-status).
#
# Two units are installed:
#   netpulse.service      — docker compose up -d on boot (oneshot, RemainAfterExit)
#   netpulse-nat.service  — re-applies the Docker MASQUERADE NAT rule after the
#                           stack is up (runs scripts/nat.sh directly)

# Install + enable both units. Resolves the repo dir + invoking user so the
# units carry the correct WorkingDirectory / User.
install_systemd_service() {
    # Resolve the repo root (the dir containing this scripts/ folder).
    local script_dir netpulse_dir netpulse_user
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    netpulse_dir="$(cd "$script_dir/.." && pwd)"
    # SUDO_USER is the real user when invoked under sudo; fall back to whoami.
    netpulse_user="${SUDO_USER:-$(whoami)}"

    echo "Installing systemd service (user=${netpulse_user}, dir=${netpulse_dir})..."

    sudo tee /etc/systemd/system/netpulse.service > /dev/null << EOF
[Unit]
Description=NetPulse Network Intelligence Platform
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=${netpulse_user}
WorkingDirectory=${netpulse_dir}
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
ExecReload=/usr/bin/docker compose restart
TimeoutStartSec=300
TimeoutStopSec=120

[Install]
WantedBy=multi-user.target
EOF

    # NAT restore — re-apply iptables MASQUERADE after the stack (and thus the
    # Docker network) exists. Runs nat.sh directly (self-applies when executed).
    sudo tee /etc/systemd/system/netpulse-nat.service > /dev/null << EOF
[Unit]
Description=NetPulse Docker NAT Rules
Requires=docker.service netpulse.service
After=docker.service netpulse.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${netpulse_dir}
ExecStart=${netpulse_dir}/scripts/nat.sh

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
    sudo systemctl enable netpulse.service
    sudo systemctl enable netpulse-nat.service

    echo "✅ NetPulse service installed and enabled"
    echo "   Start:   sudo systemctl start netpulse"
    echo "   Stop:    sudo systemctl stop netpulse"
    echo "   Status:  sudo systemctl status netpulse"
    echo "   Logs:    sudo journalctl -u netpulse -f"
}

# Disable + remove both units.
uninstall_systemd_service() {
    echo "Removing NetPulse systemd service..."
    sudo systemctl disable netpulse.service 2>/dev/null || true
    sudo systemctl disable netpulse-nat.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/netpulse.service
    sudo rm -f /etc/systemd/system/netpulse-nat.service
    sudo systemctl daemon-reload
    echo "✅ NetPulse systemd service removed"
}

# Show service status (both units).
systemd_service_status() {
    sudo systemctl status netpulse.service --no-pager || true
    echo
    sudo systemctl status netpulse-nat.service --no-pager || true
}
