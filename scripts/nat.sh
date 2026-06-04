#!/usr/bin/env bash
#
# Shared Docker MASQUERADE NAT helper.
#
# Docker containers use 172.x bridge subnets. Without NAT:
#   1. The 172.x range can collide with existing network infrastructure.
#   2. Devices that restrict SNMP/SSH by source IP reject container traffic
#      (they see the container IP, not the host).
# MASQUERADE makes all container egress appear to come from the host IP, which
# fixes both. The rule is idempotent and persisted across reboots when possible.
#
# Source this file to get detect_docker_subnet() + apply_docker_nat(); it does
# nothing on its own. Used by scripts/setup.sh, scripts/update.sh, netpulse.sh.

# Echo the NetPulse Docker bridge subnet, falling back to 172.18.0.0/16.
# The compose project is "netpulse" and the network "netpulse-net", so the real
# Docker network is "netpulse_netpulse-net" (NOT "netpulse_default").
detect_docker_subnet() {
  local net subnet
  for net in netpulse_netpulse-net netpulse_default \
             $(docker network ls --format '{{.Name}}' 2>/dev/null | grep -i netpulse); do
    subnet="$(docker network inspect "$net" 2>/dev/null | python3 -c '
import sys, json
try:
    nets = json.load(sys.stdin)
    cfg = nets[0]["IPAM"]["Config"]
    print(cfg[0]["Subnet"] if cfg else "")
except Exception:
    print("")
' 2>/dev/null)"
    [ -n "$subnet" ] && { echo "$subnet"; return 0; }
  done
  echo "172.18.0.0/16"
}

# Apply the MASQUERADE rule for the Docker subnet (idempotent) and persist it.
# Needs root/sudo for iptables. Returns non-zero only if it could not apply.
apply_docker_nat() {
  local subnet sudo
  subnet="$(detect_docker_subnet)"
  sudo=""; [ "$(id -u)" -ne 0 ] && sudo="sudo"

  if ! command -v iptables >/dev/null 2>&1; then
    echo "⚠️  iptables not found — cannot apply Docker NAT rule (subnet ${subnet})"
    return 1
  fi

  if $sudo iptables -t nat -C POSTROUTING -s "$subnet" ! -d "$subnet" -j MASQUERADE 2>/dev/null; then
    echo "✅ Docker NAT already configured (subnet ${subnet} → host IP)"
  elif $sudo iptables -t nat -A POSTROUTING -s "$subnet" ! -d "$subnet" -j MASQUERADE 2>/dev/null; then
    echo "✅ NAT configured: containers use host IP (subnet ${subnet})"
  else
    echo "❌ Failed to add Docker NAT rule — run with root/sudo (subnet ${subnet})"
    return 1
  fi

  # Persist across reboots so SNMP/SSH keep working without re-running fix-nat.
  if command -v netfilter-persistent >/dev/null 2>&1; then
    $sudo netfilter-persistent save >/dev/null 2>&1 \
      && echo "   persisted via netfilter-persistent" || true
  elif command -v iptables-save >/dev/null 2>&1 && [ -d /etc/iptables ]; then
    $sudo sh -c 'iptables-save > /etc/iptables/rules.v4' 2>/dev/null \
      && echo "   persisted to /etc/iptables/rules.v4" || true
  else
    echo "   ⚠️  install iptables-persistent to keep the rule across reboots"
    echo "      (otherwise re-run ./netpulse.sh fix-nat after a reboot)"
  fi
}
