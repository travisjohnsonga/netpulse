# Docker NAT (Container → Host IP)

NetPulse container traffic must appear to come from the **host IP**, not the
Docker bridge subnet, so that network devices which restrict management by source
IP accept SNMP/SSH/REST from NetPulse.

This is applied automatically by `scripts/setup.sh`. This guide explains what it
does, why it's required, and how to fix it if it stops working (e.g. after a
reboot).

---

## Why it's required

- Network devices commonly restrict SNMP/SSH/HTTPS management to specific
  management IPs (ACLs / "allowed management hosts").
- Docker containers, by default, source traffic from the bridge subnet
  (e.g. `172.18.x.x`), which is **not** in those device ACLs.
- A `MASQUERADE` rule rewrites container source addresses to the host IP — which
  **is** in the device ACLs.
- It also prevents the Docker bridge range from colliding with real network
  infrastructure.

**Confirmed required for:** SonicWall SNMP (SonicWall restricts management by
source IP), and any device with management source-IP ACLs.

---

## The rule

```bash
sudo iptables -t nat -A POSTROUTING \
  -s {docker_subnet} \
  ! -d {docker_subnet} \
  -j MASQUERADE
```

- Applied on the NetPulse bridge subnet — network `netpulse_netpulse-net`
  (default `172.18.0.0/16`), **not** `netpulse_default`.
- Idempotent shared logic lives in `scripts/nat.sh`
  (`apply_docker_nat` / `detect_docker_subnet`).
- Applied by `scripts/setup.sh` after the stack starts, and re-applied during
  `scripts/update.sh`.

---

## Fixing it (after a reboot, or if SNMP/SSH stops working)

```bash
sudo ./netpulse.sh fix-nat
```

> Applying the rule needs root. Run the whole command under `sudo` so the script
> skips its inner `sudo`.

---

## Persistence across reboots

The rule is persisted via `netfilter-persistent` (or `/etc/iptables/rules.v4`).
If `iptables-persistent` is **not** installed, the rule is lost on reboot — run
`sudo ./netpulse.sh fix-nat` after each reboot, or install persistence:

```bash
sudo apt-get install iptables-persistent   # Debian/Ubuntu
```

`scripts/setup.sh` installs/uses it when available.

---

## Health check

`run_health_checks` (`./netpulse.sh health`) includes a **"Docker NAT"** check.
It runs inside the `api` container, which has no host iptables access, so it
**WARNs** ("verify on the host") rather than failing. Apply/verify on the host
with `sudo ./netpulse.sh fix-nat`.

---

## Troubleshooting

| Symptom                                          | Fix |
|--------------------------------------------------|-----|
| SNMP/SSH works, then stops after a reboot        | `sudo ./netpulse.sh fix-nat`; install `iptables-persistent`. |
| SonicWall SNMP never responds                    | Confirm NAT rule is present and the device allows the host IP. |
| Health check shows "Docker NAT" WARN             | Expected from inside the container — verify on the host. |
| Containers source from `172.18.x.x`              | NAT rule missing — re-apply with `fix-nat`. |

See also: ARCHITECTURE.md → **Network Architecture → Container NAT**.
