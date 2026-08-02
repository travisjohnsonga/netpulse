# Juniper Junos Integration Guide

spane supports Juniper **Junos** devices (platform `junos`). Everything below
was **live-verified** (vSRX 23.2R2.21; a real EX4100-24MP SNMP capture for the
EX-family notes) — this platform has several sharp edges that generic
multi-vendor flows miss.

> 🔒 This guide contains **no credentials**. Store device credentials in
> OpenBao (Settings → Credentials); lab-specific identifiers live in the
> gitignored `LOCAL_NOTES.md`.

## Config push — commit is mandatory, private mode is used

Junos loads `set` lines into a **candidate** config; nothing takes effect
without an explicit commit. spane's push path (`push_junos_private` in
`apps/config_templates/push.py`, used by both the config-template and
telemetry pushes) therefore:

- enters **`configure private`** — an isolated candidate that commits
  independently and never sweeps a concurrent operator's half-finished shared
  edits into an automated commit. Junos *refuses* private entry while the
  shared candidate is dirty, which fails the push loudly instead of silently
  merging someone's work;
- runs an explicit **`commit comment "spane config push"`** — a commit
  failure (Junos validates semantics at commit, not load) is surfaced as a
  real push failure and the candidate is discarded (`rollback 0`);
- rejects the push when the CLI **prints** a load error (`syntax error`,
  `error:` …) — Junos reports rejected lines in output rather than raising,
  which would otherwise commit a partial config.

## SNMPv3 CLI keywords (verified via CLI completion)

| Protocol | Junos keyword |
|---|---|
| SHA-1 | `authentication-sha` — **`authentication-sha1` does not exist**; the CLI rejects the line at that token |
| SHA-224/256/384/512 | `authentication-sha224` / `-sha256` / `-sha384` / `-sha512` — each its own keyword; never downmap (protocol mismatch with the poller) |
| Plaintext secrets | `authentication-password` / `privacy-password` — **`-key` variants expect a pre-localized key**; pushing plaintext into them yields "Wrong SNMP PDU digest" on every poll |
| Privacy | `privacy-aes128` / `privacy-3des` (23.2 exposes no des / aes192 / aes256) |

## Streaming telemetry (JTI / gNMI) — support is NOT uniform

- **SRX family (incl. vSRX): no JTI gRPC dial-out at all.** The
  `services analytics streaming-server` statement is absent from the platform
  grammar, and `commit check` rejects sensors without one. SRX streaming
  telemetry is gNMI **dial-in** (`system services extension-service`), which
  spane does not drive yet (roadmap: gNMI capability discovery). spane's gNMI
  generator emits guidance comments for `srx` models instead of unpushable
  config.
- **Elsewhere, support is line-card/FPC granular** per Juniper's own CLI
  reference (MX by MPC generation, PTX by FPC revision; EX/QFX never listed
  for this hierarchy) — do not assume support by chassis family; the roadmap
  pin is a live capability probe. Resource paths with bracket predicates must
  be **double-quoted** (`resource "/components/component[name='Routing
  Engine']/state"`) — the CLI tokenizes at the embedded space otherwise.

## Environment / inventory (EX-family notes)

- EX4100/EX4400 implement **entPhysicalTable** (PSUs class 6, fans class 7 —
  correct names like `JPSU-920W-AC-AFO`) but **not ENTITY-SENSOR-MIB at all**
  (`No Such Object` across `1.3.6.1.2.1.99.*`). spane therefore shows EX
  PSU/fan units as **presence-only** (status "unknown"); per-unit status and
  readings require the proprietary **`jnxOperatingTable`**
  (`1.3.6.1.4.1.2636.3.1.13.1`) — roadmap, walk-and-verify first.
- PoE (`pethMainPseTable`) is deliberately **not** walked on junos yet: the
  budget math currently applies the AOS-CX half-watt divisor, which would
  halve a Juniper true-watt budget (platform-aware divisor is a pinned
  follow-up).

## Enrichment

`os_version` parses from sysDescr (`… kernel JUNOS 23.4R1.9 …` →
`23.4R1.9`), model from the token after the company name (ENTITY-MIB wins
when present). See `_parse_junos_descr` in `apps/devices/enrich.py`.
