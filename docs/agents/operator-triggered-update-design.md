# Design: Operator-triggered agent update (signing-first)

> **Status: DESIGN — not built.** For review. This is the highest-RCE-risk feature
> in the product (an agent executing a server-pulled binary on many privileged
> hosts), so the design is **staged with binary signing as a hard gate**: Stage 2
> (the "click to Update" UI) **cannot ship until Stage 1 (signing + pinned-key
> verification) exists and is load-bearing.**

## The risk (why signing gates everything)

Today the updater (`agent/scripts/update-agent.sh` / `Update-Agent.ps1`, #112)
downloads the binary from `{server}/agent/download/<platform>` and only verifies
it **runs and reports a version** before swapping — it does **not** verify
**authenticity**. A malicious binary also runs and reports a version. So a
compromised release path, a tampered `agent/dist/` mount, or a MITM on the
download = **remote code execution on every agent**, each running as
root/LocalSystem. Operator-triggered update *automates* that pull across the
fleet, raising the stakes. **Signature verification against a key pinned in the
agent is the only control that closes this**, and it must exist before any
server-pulled binary is executed automatically.

---

## Stage 1 — Binary signing + pinned-key verification (THE GATE)

### 1.1 Signing key

An **Ed25519** signing keypair (small, fast, well-supported; or ECDSA P-256 if an
HSM mandates it). A **detached signature** is produced over the binary's bytes
(equivalently its SHA-256).

**Where the PRIVATE key lives — the core decision:**

| Option | Pros | Cons |
|--------|------|------|
| **OpenBao** (transit engine sign/verify) | Already in the stack; key never leaves OpenBao (sign-as-a-service, the raw key is never exported); access-controlled + audited; revocable | **A compromised OpenBao can sign malicious binaries.** OpenBao is online and part of the same blast radius as the server — the very thing signing is meant to protect against. |
| **Offline / HSM** (air-gapped signing station or a hardware token; e.g. a YubiKey/PKCS#11 or cosign with a hardware key) | The signing key is **not reachable from the running stack** — compromising the server/OpenBao does NOT let an attacker sign; strongest authenticity guarantee | Operationally heavier: releases require a human + the offline key; doesn't fit a fully-automated CI release without a signing service. |

**Recommendation: offline/HSM signing for the *release* key, NOT OpenBao.**
Rationale: the entire point of signing is to survive a server/OpenBao compromise.
If the same OpenBao that holds device secrets can also mint agent binaries, an
attacker who owns the server owns the fleet anyway — signing adds little. Keeping
the signing key **off the online stack** is what makes the control meaningful.
OpenBao's transit engine is acceptable **only** as an interim/lower-assurance
option with the explicit understanding that it does not protect against a
server-side compromise. **→ Travis decision: offline/HSM vs OpenBao-transit
(assurance vs operational cost).**

The private key MUST never appear in the repo, CI logs, container images, or env
vars. (OpenBao-transit satisfies this by never exporting the key; offline
satisfies it by never being online.)

### 1.2 CI signs releases

`build-agent.yml` produces, per released binary, a **detached signature**
(`netpulse-agent-<platform>.sig`) published alongside the binary in the GitHub
Release.

How CI accesses the key **without exposing it**, by key-storage choice:
- **Offline/HSM (recommended):** CI builds + uploads the **unsigned** artifact;
  a separate **offline sign step** (human at the signing station, or a dedicated
  signing service the key is bound to) signs the released binary and uploads the
  `.sig`. CI never holds the key. Cleanest with a tagged-release gate (signing
  happens on release, not every main push — aligns with #125's tags-only
  republish).
- **Signing service:** CI calls a narrow "sign this digest" service
  (OpenBao-transit or a custom signer) authenticated via short-lived OIDC (GitHub
  Actions → OpenBao JWT auth), with a policy that allows **sign only**, not key
  export. The digest goes out, the signature comes back; the key never reaches CI.
- **Never:** a raw key in a CI secret (a leaked Actions log / malicious workflow
  edit would exfiltrate it).

### 1.3 Agent ships the PINNED public key

The agent embeds the trusted **public** key **at build time** (compiled into the
binary, e.g. a `const` or `//go:embed`). Verification uses the **pinned** key.

**CRITICAL:** the public key is **pinned in the agent and never fetched from the
server.** If the agent fetched the key from the server, a compromised server
could swap **both** the binary and the key — defeating the entire control. The
trust anchor must live in the artifact the attacker is trying to replace, not in
the channel they may control.

**Rotation (the hard problem).** Pinned keys are deliberately hard to rotate —
that's the security property, but it's also the operational cost. Design for it
up front:
- **Pin a SET, not a single key.** The agent embeds an array of accepted public
  keys (current + next). A signature valid under **any** pinned key passes. To
  rotate: (1) release an agent build that pins {old, new}; (2) wait for the fleet
  to reach that build (verified via reported version); (3) start signing with
  `new`; (4) a later build drops `old`. This gives a migration window without a
  flag day.
- **Compromise of the signing key** is the worst case: it requires shipping a new
  pinned-key agent build *through a channel the compromised key can no longer
  authenticate* — i.e. a **manual re-install** (the existing one-line installer
  with a fresh binary). Document this as the break-glass path; it's unavoidable
  with pinning and is the correct trade (pinning is what makes the common case
  safe). **→ Travis decision: pin-a-set rotation vs single-key + manual-reinstall
  break-glass.**

### 1.4 Verify-before-swap (two distinct checks, both kept)

The updater gains a **signature check** that is separate from the existing
run-check:
- **Authenticity (NEW):** download the binary **and** its `.sig`; verify the
  signature against the **pinned public key**. **Refuse on mismatch.** A failure
  here is a **security event** (possible compromise / MITM / tampered release) —
  logged and reported distinctly (see Stage 2 `update_status`), not lumped with a
  routine failure.
- **Functionality (EXISTING #112):** the verified binary runs and reports a
  sane version before the swap; backup + rollback-on-failure remain.

Order: **verify signature → verify runs → backup → atomic swap → restart →
health-gate → rollback on fail.** Signature is checked first (cheapest, and a bad
signature should never be executed even for the run-check — verify the bytes
before running them).

**This stage gates Stage 2. Until signing exists and is load-bearing, there is no
server-pulled-binary auto-execution.**

---

## Stage 2 — Operator-triggered update (pull-based; outbound-only preserved)

### 2.1 Pull-based `desired_version` (NOT an inbound command)

Mirrors the existing `desired_config` pull (no inbound channel to the agent):
1. Operator clicks **Update** (or multi-select) → server sets
   `Agent.desired_version` (desired *state*, audited). **No connection is made to
   the agent.**
2. The agent reads `desired_version` from its **metrics check-in RESPONSE** (same
   place it reads `desired_config`, ~`agent.go:198`).
3. If `desired_version` ≠ running version → the agent self-updates: **download →
   verify SIGNATURE (Stage 1) → verify runs → backup → atomic swap → restart →
   rollback-on-failure** (reuse #112's updater).

Outbound-only is preserved — no new ports, no inbound trigger. Same small-attack-
surface model as metrics/config/log-forwarding.

### 2.2 `update_status` reporting (push, on check-in)

The agent reports, in each metrics push:
`{ state, current_version, target_version, error_reason, attempted_at }`.

States: `up_to_date` · `update_available` · `update_pending` (desired set, not yet
attempted) · `updating` · `updated` · `update_failed` (+`error_reason`).

- **Restart handling:** on success the **new** process reports `updated`; on
  failure the **old** (rolled-back) process reports `update_failed` + reason.
- **STICKY failure:** attempt **once**, report the failure, and **stop** — do not
  retry-loop (a bad release must not have the whole fleet thrashing downloads/
  restarts). A new operator action (or a changed `desired_version`) is required to
  re-attempt.
- **Signature failure** is flagged as a **SECURITY** signal, distinct from a
  routine `update_failed` (it may indicate a compromised release/MITM, not a bad
  build).

### 2.3 Agents table UI

Per-agent: a **status badge** (up-to-date / update-available / updating / updated
/ failed) + an **Update** button when an update is available, becoming
`updating…` → `✓ updated to vX` / `✗ failed: <reason> [Retry]`.

- **Per-agent or explicit multi-select only** — **never an unattended "update
  all".** Bounded blast radius is a feature: canary a few, verify healthy, then
  widen. (Mirrors the staged-rollout principle from the auto-update roadmap.)

### 2.4 RBAC + audit

- New `agent:update` capability (deny-by-default; Admin/Engineer only).
- Every trigger **audited**: who, when, which agent(s), from-version → to-version.
  Signature failures audited as security events.

---

## Intersecting deferred TODOs

- **Migration safety does NOT apply here.** This is the **agent** update — the
  agent is **stateless** (no DB, no migrations), so the pg_dump/migration-backup
  concern (issue #148) is irrelevant to it. **The separate, future APP-SERVER
  update** is where migration safety matters; keep the two flows clearly distinct
  in any implementation. (Agent = signed-binary swap + rollback; app server =
  binary + **DB migration + pre-migration backup** as a two-part rollback.)
- **The manual flow stays.** The copy-command on the Agents page (and the
  pre-placed `netpulse-agent-update.sh`) remain as the fallback / for operators
  who prefer manual updates. Operator-triggered is additive, not a replacement.

---

## Security properties (the design's guarantees)

- **Outbound-only preserved** — pull-based `desired_version`, no inbound channel.
- **Signature-gated** — no server-pulled binary executes without a valid signature
  against the **pinned** public key; signature failure is a security event.
- **Trust anchor not server-controlled** — the public key is pinned in the agent,
  never fetched, so a compromised server can't swap key+binary together.
- **Rollback-safe** — verify-runs + backup + health-gate + rollback (kept from #112).
- **Sticky failure** — one attempt, reported, no fleet-thrashing retry loop.
- **No unattended fleet update** — per-agent / explicit multi-select, bounded blast
  radius.
- **RBAC + audited** — `agent:update`, who/when/to-what recorded.

## Open decisions for Travis

1. **Signing-key storage:** offline/HSM (recommended, strongest) vs OpenBao-transit
   (convenient, but doesn't survive a server compromise).
2. **Key rotation:** pin-a-set (current+next, smooth rotation) vs single-key +
   manual-reinstall break-glass.
3. **Build now vs post-evaluation:** this is a pre-eval-sensitive feature (adding a
   self-updating RCE mechanism right before a security review). Recommend building
   **Stage 1 (signing) now** as general hardening (it strengthens even the manual
   updater), and **Stage 2 (operator UI) post-eval**, so the eval sees a signed,
   staged, rollback-safe design rather than an unsigned auto-updater.
