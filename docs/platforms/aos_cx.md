# HPE AOS-CX Integration Guide

NetPulse supports HPE Aruba Networking **AOS-CX** switches (platform `aos_cx`),
validated against a real **HPE 6100** (`wco2-idf5-asw-01`). AOS-CX is one of the
few platforms with full **environment telemetry** (CPU/memory/temperature/fans/
PSU) in NetPulse — all over SNMP.

> 🔒 This guide contains **no credentials**. Store device credentials in OpenBao
> (Settings → Credentials) or, for local lab reference, in the gitignored
> `LOCAL_NOTES.md`.

---

## At a glance

| Capability      | Support                                              |
|-----------------|------------------------------------------------------|
| SNMP            | ✅ SNMPv3 authPriv (CPU/mem/temp/fan/PSU)             |
| SSH             | ✅ (Netmiko `aruba_aoscx`)                            |
| Config backup   | ✅                                                    |
| ARP/MAC         | ✅ (ntc-templates ships `aruba_aoscx`)                |
| Environment     | ✅ (SNMP — see below)                                 |
| gNMI            | 📋 OpenConfig dial-out on port 8443 (planned)        |
| REST API        | ⚠️ not on the 6100 (login 400/401); higher-end may   |

---

## Detection

- **sysDescr**: `HPE ANW {model} {firmware}`, e.g.
  `HPE ANW R9Y04A 6100 48G CL4 4SFP+ Sw PL.10.16.1030`.
- **sysObjectID**: enterprise `47196` (HPE Networking), e.g.
  `1.3.6.1.4.1.47196.4.1.1.1.260`.
- **SSH banner**: generic OpenSSH (no platform hint) — detection relies on
  Netmiko SSHDetect (`aruba_aoscx`), sysDescr, or sysObjectID.
- SSHDetect `aruba_aoscx` → `aos_cx`; sysDescr `HPE ANW` / `HPE Aruba` → `aos_cx`
  (not just `ArubaOS-CX`); sysObjectID enterprise 47196 → `aruba`/`aos_cx`.

---

## SNMP Enrichment (verified, HPE 6100)

- **Model**: parsed from the HPE/ANW sysDescr
  (`HPE ANW R9Y04A 6100 48G CL4 4SFP+ Sw …` → `R9Y04A 6100 48G CL4 4SFP+ Sw`).
- **OS version**: trailing firmware token (`PL.10.16.1030`).
- **Serial**: `entPhysicalSerialNum` via **WALK** — the chassis row sits at a
  vendor index (e.g. `112001`), **not** `.1`, so a scalar `.1` GET comes back
  empty. NetPulse walks the column and takes the first real value.

## Environment Telemetry (SNMP-only)

AOS-CX exposes environment metrics at vendor-specific indexes, so the poller
gained table **WALK** support (`walk_oids` in the device payload) alongside the
existing GETs. Derivation lives in `apps/telemetry/snmp_environment.py` (pure,
unit-tested); the stream-processor merges scalars into the `telemetry`
measurement and writes one `device_environment` point per temperature sensor.

| Metric        | OID / source                                      | Notes |
|---------------|---------------------------------------------------|-------|
| CPU           | `hrProcessorLoad` `1.3.6.1.2.1.25.3.3.1.2`         | At vendor indexes (196608/196609), **not** `.1` — WALK and average. |
| Memory        | `hrStorage` index 1 ("Physical memory")            | GET `.5.1`/`.6.1`/`.4.1` → `memory_used_pct` + bytes. |
| Temperature   | ENTITY-SENSOR-MIB `1.3.6.1.2.1.99.1.1.1.*`         | Celsius = `value × 10^scale × 10^-precision` (28875 milli → 28.875 °C). |
| Fans / PSU    | ENTITY-MIB `entPhysicalClass` (fan=7, psu=6)       | Presence/count + names (see 6100 limits). |
| Per-fan RPM   | ENTITY-SENSOR `entPhySensorOperStatus`             | RPM reads `-1` (unavailable) on the 6100. |
| Per-PSU watts | ENTITY-SENSOR `entPhySensorOperStatus`             | Watts read 0 on the 6100. |
| PoE           | POWER-ETHERNET-MIB `pethMainPseTable` (WALK)       | AOS-CX reports budget at 2× (half-watts; 740 → 370 W). |

Stream-processor emits scalars `cpu_pct`, `memory_used_pct`, `memory_*_bytes`,
`temp_max_c`, `fan_count`, `psu_count`, and `device_environment` points
(tags `device_id`, `sensor_name`, `sensor_type`; fields `temperature_c`,
`status_ok`). Temperature alert rules are seeded: **High Temperature Warning**
(medium, ≥75 °C), **High Temperature Critical** (critical, ≥85 °C),
**Temperature Sensor Failed** (high).

### 6100 limitations
- Per-unit fan **RPM** is unavailable (RPM sensors read `-1`) and there is no
  standard per-unit fan/PSU **oper-status** (the entPhysical `.8` column is
  `entPhysicalHardwareRev`, not a status). Fan/PSU is reported as
  **presence/count** only; reliable status exists only for sensors
  (`entPhySensorOperStatus`). Higher-end models (8xxx) may expose more.
- **REST API** is **not** supported on the 6100 (login 400/401) — SNMP only.
  Higher-end models may support the AOS-CX REST API
  (`apps/devices/aos_cx_client.py`, used as the preferred enrichment path when
  available).

---

## SNMPv3 reliability

The poller creates a **fresh `SnmpEngine` per poll** to avoid stale
engineBoots/engineTime (general robustness).

The "Wrong SNMP PDU digest" error seen in the lab was **not** the engine — it was
a **wrong stored SNMPv3 auth/priv key**: the credential profile's passphrase in
OpenBao did not match the device. With the correct key both pysnmp 6.3 (ingest)
and 7.1 (api) succeed. **Fix:** update the credential in Settings → Credentials.

---

## Aruba Central (cloud-managed)

The lab 6100 is Aruba Central-managed
(`device-prod-d2.central.arubanetworks.com`).

- **Keepalive logs are NORMAL**: `hpe-restd` AMM/UKWN messages arrive roughly
  every ~30 s. These are cloud-management heartbeats, **not** errors — hide them
  with Log Filters if they add noise.
- **Config push on Central-managed AOS-CX** (when implemented) requires
  temporarily disabling Central (`aruba-central disable`), a mandatory ~2 s wait,
  the push, then `aruba-central enable` — always re-enable in a `finally` block.
  See the pinned "AOS-CX Central Managed Config Push Pattern" in CLAUDE.md.

---

## SSH / ARP / Config

- Netmiko `device_type`: `aruba_aoscx`.
- ARP/MAC collection works out of the box — ntc-templates 9.1.0 ships
  `aruba_aoscx` templates.
- Config backup over SSH (or REST on models that support it).

---

## gNMI (planned)

AOS-CX supports native OpenConfig gNMI dial-out on **port 8443** (TerminAttr not
needed). NetPulse's ingest-grpc currently targets Cisco MDT on 57400; AOS-CX gNMI
support is planned. OpenConfig paths:

```
CPU:        /system/cpus/cpu[index=0]/state/usage
Memory:     /system/memory/state
Interfaces: /interfaces/interface/state/counters
BGP:        /network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor
```

---

## Troubleshooting

| Symptom                                  | Cause / Fix |
|------------------------------------------|-------------|
| "Wrong SNMP PDU digest"                  | Wrong SNMPv3 key in OpenBao — fix in Settings → Credentials. |
| Serial/model empty over SNMP             | Chassis at a vendor index, not `.1` — NetPulse WALKs the column. |
| Fan RPM / PSU watts show 0 / -1          | 6100 limitation — presence/count only. |
| REST enrichment fails (400/401)          | 6100 has no REST API — falls back to SNMP automatically. |
| Constant `hpe-restd` log noise           | Normal Aruba Central keepalives — filter with Log Filters. |
| SSH host key verification failure        | Firmware update changed the key — `ssh-keygen -R {device_ip}`. |
