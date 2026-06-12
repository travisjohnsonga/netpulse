# SonicWall (SonicOS / SonicOSX) Integration Guide

spane supports SonicWall firewalls across both major OS generations:

- **v7** — SonicOS 7.x (e.g. TZ 670, NSa series)
- **v8** — SonicOSX 8.x (e.g. NSv, NSsp series)

The two generations differ in REST API port, authentication outcome, and SNMP
OID layout. spane handles both automatically, but the differences matter for
troubleshooting — especially config backup on v7.

> 🔒 This guide contains **no credentials**. Store device credentials in OpenBao
> (Settings → Credentials) or, for local lab reference, in the gitignored
> `LOCAL_NOTES.md`.

---

## At a glance

| Capability      | v7 (SonicOS 7.x)        | v8 (SonicOSX 8.x)     |
|-----------------|-------------------------|-----------------------|
| REST API port   | 4444                    | 443                   |
| REST auth       | RFC-7616 Digest SHA-256 | RFC-7616 Digest SHA-256 |
| Config backup   | ⚠️ built-in `admin` only | ✅ any admin account  |
| SNMP            | ✅ `…8741.1.3.2.x`       | ✅ `…8741.1.3.1.x`    |
| SSH (ARP)       | ✅ (double password)    | ✅ (double password)  |
| Environment     | ❌ not exposed          | ❌ not exposed        |

---

## Authentication

Both generations use **RFC-7616 HTTP Digest (SHA-256)** for the REST API
(`requests.auth.HTTPDigestAuth`). Basic auth is disabled by default in SonicOSX 8.

Login is `POST /api/sonicos/auth` with body `{"override": true}`. The response
`status.info[0]` carries the outcome:

| `auth_code`                | Meaning                                              |
|----------------------------|------------------------------------------------------|
| `API_AUTH_SUCCESS`         | Full API access (built-in admin, or any v8 admin).   |
| `API_AUTH_USER_CAN_MGMT`   | User account — management UI only, **no** `/config/current`. |

- **v7**: only the built-in `admin` returns `API_AUTH_SUCCESS`. Other admin
  users (even with FULL_ADMIN) return `API_AUTH_USER_CAN_MGMT`.
- **v8**: all admin accounts return `API_AUTH_SUCCESS`.

Sessions are limited — spane always calls `logout()`
(`DELETE /api/sonicos/auth`); the `SonicWallClient` context manager does this for
you. Client: `apps/compliance/sonicwall_client.py`.

### TLS gotcha
SonicWall management certs are self-signed, so spane uses `verify=False`. The
api image sets `REQUESTS_CA_BUNDLE`, and `requests`' env-merge would turn a
per-request `verify=None` into that bundle **before** `session.verify=False`
applies. `SonicWallClient` therefore sets `session.trust_env=False` **and**
passes `verify=` on every call. Just setting `session.verify=False` is silently
ignored.

---

## Config Backup

spane prefers the REST API over SSH for config backup and enrichment:

```
GET /api/sonicos/config/current
```

Returns top-level `model`, `serial_number`, `firmware_version`, `system_uptime`,
plus the full JSON config. `administration.firewall_name` is the hostname.

### v8 — works with any admin account
Any admin account can read `/config/current`. Put the HTTPS/API credentials in
the device's credential profile (`https_username` / `https_password`,
`https_port` = 443) and config backup runs automatically.

### v7 — REQUIRES the built-in `admin` account ⚠️
This is the single most important SonicWall caveat:

- User accounts (even FULL_ADMIN) return **401** on `/config/current`.
- The auth response is `API_AUTH_USER_CAN_MGMT`, not `API_AUTH_SUCCESS`.
- There is no privilege you can grant a user account to fix this — it is a
  SonicOS 7 platform limitation.

**Workarounds:**
1. Use the **built-in `admin`** account in the device's HTTPS credential profile
   (REST port 4444 for v7).
2. Or use the **SSH CLI backup** path instead of REST.

---

## SSH (ARP Collection)

SonicWall has **no Netmiko SonicOS driver**, and the SonicOS login banner
interrupts Netmiko's generic-driver auth (the banner prints before the
`Password:` prompt). spane collects over a **direct paramiko SSH shell**
(`apps/arp_mac` → `_collect_sonicwall_arp` / `_drive_sonicwall_shell`):

1. Connect with `banner_timeout=30` / `auth_timeout=30` / `look_for_keys=False`
   / `allow_agent=False`, then `invoke_shell()`.
2. **Double password** — SonicWall re-prompts for the password on the interactive
   shell even after paramiko has authenticated the SSH session (the banner ends
   with `Access denied\nPassword:`). The collector re-sends the **same** password
   when it sees that prompt. This is normal SonicOS behaviour; both prompts take
   the same password.
3. Send `no cli pager session` as its **own** command and drain the response to
   disable paging.
4. Send `show arp caches` and read the full (unpaged) reply — no `--More--`
   handling needed.

A custom TextFSM template
(`apps/collectors/templates/sonicwall_show_arp_caches.textfsm`) parses the
IP/Type/MAC/Vendor/Interface/Timeout columns. The device-reported vendor is
dropped — spane derives it from the MAC OUI. SonicWall is **ARP-only**
(firewalls have no MAC address-table).

---

## SNMP

SNMPv3, enterprise OID `1.3.6.1.4.1.8741`. The CPU/memory subtree **moved**
between major releases, so spane polls **both** and uses whichever returns a
non-zero value.

| Metric       | v7 (SonicOS 7.x)        | v8 (SonicOSX 8.x)          |
|--------------|-------------------------|----------------------------|
| CPU          | `…8741.1.3.2.3.0` (%)   | `…8741.1.3.1.3.0` (%)      |
| Memory       | MemUsed `…1.3.2.2.0` (KB) + MemTotal `…1.3.2.1.0` (KB) → `used/total×100` | `…8741.1.3.1.4.0` (% direct) |
| Connections  | —                       | `…8741.1.3.1.2.0`          |

- Confirmed live: **v7 TZ 670** (SonicOS 7.3.2) responds to `1.3.2.x`, NOT
  `1.3.1.x`. **v8 NSv XS** (SonicOSX 8.2.1) responds to `1.3.1.x`.
- Implementation: `PLATFORM_DEVICE_OIDS["sonicwall"]` polls both subtrees;
  `FIELD_MAP` maps `1.3.1.x` → `cpu_pct`/`memory_used_pct`/`connections` and
  `1.3.2.x` → `cpu_pct_alt`/`memory_used_kb`/`memory_total_kb`;
  `query_device_metrics` prefers the v8 values and falls back to the v7 pair.
- sysDescr: `SonicWALL {model} ({os_details})` → parsed for model/os_version
  (`_parse_sonicwall_descr`). Serial from `snwlSysSerialNumber`
  (`1.3.6.1.4.1.8741.1.3.1.1.0`) when `entPhysicalSerialNum` is empty.
- Netmiko has no SonicOS driver → SSH `device_type` falls back to `generic`
  (`sonic_os` is not a valid Netmiko type).

### Docker NAT required
SonicWall restricts management access by source IP, so containers **must**
MASQUERADE-NAT to the host IP. Confirmed required for SonicWall SNMP. See
[docs/setup/nat.md](../setup/nat.md).

---

## Environment Data (not available)

SonicWall does **NOT** expose temperature, fan, or PSU data via SNMP or the REST
API on any version. The spane Environment tab correctly shows
**"No environment data"** for SonicWall. CPU and Memory are available on the
**Telemetry** tab.

---

## Known limitations summary

- **v7 config backup** needs the built-in `admin` account (user accounts get
  401 on `/config/current`).
- **No environment data** (temp/fan/PSU) on any version.
- **No Netmiko driver** — ARP collection uses direct paramiko with a double
  password handshake.
- **No MAC address-table** — firewalls are ARP-only.
- **Source-IP restricted management** — Docker NAT to host IP is required.

---

## Troubleshooting

| Symptom                                   | Cause / Fix |
|-------------------------------------------|-------------|
| Config backup 401 on v7                   | User account — use the built-in `admin` (REST port 4444). |
| `verify=False` ignored, TLS errors        | Ensure `session.trust_env=False` + per-call `verify=` (handled by `SonicWallClient`). |
| SNMP returns zeros                         | Wrong OID subtree for the OS version — spane polls both; confirm SNMPv3 keys + Docker NAT. |
| ARP collection hangs / `Access denied`     | Double-password prompt — confirm the same password is re-sent on the shell. |
| ARP output truncated with `--More--`       | `no cli pager session` not sent first. |
| Environment tab empty                      | Expected — SonicWall exposes no environment sensors. |
