# FortiOS (FortiGate / Fortinet) Integration Guide

NetPulse supports Fortinet FortiGate firewalls (platform `fortios`). FortiOS has
**no gNMI** — telemetry is SNMP + Syslog + NetFlow.

> 🔒 This guide contains **no credentials**. Store device credentials in OpenBao
> (Settings → Credentials) or, for local lab reference, in the gitignored
> `LOCAL_NOTES.md`.

---

## At a glance

| Capability    | Support                                                    |
|---------------|------------------------------------------------------------|
| SNMP          | ✅ Fortinet enterprise OIDs (needs a valid FortiOS license) |
| Syslog        | ✅                                                          |
| NetFlow       | ✅                                                          |
| gNMI          | ❌ not supported                                            |
| Config backup | ✅ SSH (`show full-configuration`)                          |
| ARP           | ✅ (ARP-only — firewalls have no MAC address-table)         |
| Environment   | ❌ not collected                                            |

---

## Detection

- SSH-banner auto-detection covers FortiOS/PAN-OS that Netmiko `SSHDetect` misses.
- sysDescr contains `FortiOS` / `FortiGate` / `Fortinet` → platform `fortios`.
- Bulk-approve of unknown-platform devices applies the vendor default when the
  vendor is known (Fortinet → fortios).

## Telemetry (SNMP)

Fortinet enterprise OIDs (`1.3.6.1.4.1.12356.101`):

- CPU:            `fgSysCpuUsage`     `…4.1.3.0`
- Memory used %:  `fgSysMemUsage`     `…4.1.4.0`
- Memory cap:     `fgSysMemCapacity`  `…4.1.5.0`

Surfaced in the telemetry FIELD_MAP (`cpu_pct`, `memory_used_pct`); a direct
`memory_used_pct` is honored.

### License requirement
SNMP needs a valid FortiOS license. On unlicensed/eval VMs the SNMP daemon can't
read `vm.lic` and FortiOS emits `Secure Module Access Violation`
(`secappdomain=SNMPD`). The syslog normalizer tags these `fortios_license_warning`
with an explanatory note.

## Interface discovery

Platform-aware: uses `get system interface` + a custom parser (FortiOS does not
present a standard ifTable cleanly); LLDP is best-effort.

## Config backup

- FortiOS has no `show running-config` — collection uses **`show full-configuration`**.
- The header lines `#config-version` / `#conf_file_ver` / `#buildno` /
  `#global_vdom` are **stripped before change-detection hashing** — they drift per
  session and `#config-version` embeds the running user, which would otherwise
  flag a spurious change every collection.

## Benign / expected log noise

Every NetPulse config-collection SSH session makes the Netmiko `fortinet` driver
disable paging, which FortiOS logs as a `cfgpath=system.console` config event.
The syslog normalizer tags these `fortios_benign=true` (severity floored to info)
— they are **not** real config changes. Expect frequent SSH sessions from the
collector; this is normal. Use Log Filters to suppress if noisy.

## SNMPv3

authPriv config generation per platform uses FortiOS token mapping (privacy
`aes`). SNMPv2c generated config carries a plaintext security warning.
