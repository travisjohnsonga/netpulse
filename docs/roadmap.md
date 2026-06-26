# Roadmap — Designed but Not Built

This page captures ideas that have been **considered and deliberately deferred** —
not committed work, and not a backlog with dates. Each entry records the *why*
(the rationale and the tradeoffs that led to deferring it) and the key design
considerations, so whoever picks one up starts with context rather than just a
title.

Entries are ordered roughly by a mix of **value and readiness** — nearest-term,
build-on-what-exists items first; larger or gated arcs later. Anything genuinely
in flight lives in the code and the README "Current State", not here.

!!! note "Status legend"
    **Near-term** = small, builds directly on shipped work · **Design pass** =
    wants its own design before coding · **Arc** = a multi-part feature
    comparable to a major epic · **Gated** = explicitly blocked behind another
    milestone (e.g. the security evaluation).

---

## Agent health: distinguish "online" from "reporting" — *Near-term · highest priority*

**The gap.** An agent currently shows a green **Online** badge based purely on
heartbeat liveness (`status == active` and `last_seen` within 5 minutes). That can
be true while the host is actually **asleep, down, shut down, or unreachable** and
delivering nothing — the badge reads *Online* off a **stale `last_seen`**. The
cleanest real example: the lab **host sleeps after ~4h of inactivity** (a
cost-saving artifact); the agent goes silent, but the badge can still read
*Online*. A monitoring tool should never read "fine" when it isn't actually
collecting.

The Degraded/Offline states need to distinguish the underlying causes:

- **Host unreachable / asleep / shut down → the heartbeat itself goes stale**
  (the agent isn't checking in at all — likely *down*).
- **Host up but ingest failing → heartbeat fresh, ingest stale** (the agent is
  checking in but its metrics pushes aren't landing — *up but not collecting*).
  This is the 502-during-rebuild case: every metrics `POST` returned 502 while
  `last_seen` kept refreshing, so the badge stayed green for ~2 days.

**The idea.** Add a **DEGRADED / WARNING** state shown when the heartbeat is
fresh but the **last successful metrics push is stale**. Track the last
successful-ingest timestamp distinctly from `last_seen`, and surface the
three-way state (Online / Degraded / Offline) on the Servers list, the server
detail header, and the site server-counts.

**Why it's first.** This is a **correctness gap in core monitoring**, not a new
capability — higher priority than everything below. It builds on existing fields
(`last_seen`, the metrics handler) and the up/down plumbing already in place.

**Considerations.** Decide the "stale push" threshold relative to
`collection_interval`; ensure the metrics handler records success vs failure
distinctly (today it stamps `last_seen` on receipt); keep the Servers-page
`isOnline` logic and the backend site-count logic in agreement.

**Watchdog / alerting (the action layer).** Beyond *showing* Degraded/Offline,
fire an **alert** when an agent stops reporting for a **configurable** threshold
(no successful metrics ingest for X minutes). The badge makes the truth visible;
the watchdog notifies without someone watching the badge.

- **Configurable threshold.** A global default plus per-agent / per-site override
  (a critical server warrants a tighter window than a lab box). Relate the default
  to `collection_interval` (N missed intervals).
- **Default for PRODUCTION always-on hosts.** A real server silent for even a few
  minutes is a genuine incident (crash, network loss, OOM, shutdown) — never
  benign. Do **not** tune the global default lenient to accommodate the lab's
  4h-idle sleep (a cost-saving artifact that doesn't exist in production); treat
  the lab as the exception (a longer threshold / suppressed alerts on the lab
  agent). In production every "agent went silent" is signal.
- **Reuse the existing alerting plumbing** (the platform already has Alerts) so an
  agent-silence alert flows through the same notification path, not a parallel one.
- **One staleness signal, two consumers.** Built on the SAME signal as the
  Degraded-state work (last-successful-ingest timestamp vs now) — display and alert
  are two consumers of one staleness signal, so build them together.
- **Distinguish conditions / severities.** Heartbeat stale = likely down
  (critical) vs heartbeat fresh + ingest stale = up-but-not-collecting (warning).
- **Avoid alert storms.** Debounce / dedupe (one alert per agent-down event) and
  auto-resolve when reporting resumes.

So the entry covers the full loop: detect staleness → show the honest state
(Online / Degraded / Offline) → alert at a configurable threshold → auto-resolve
on recovery.

---

## Fleet: "agents needing update" view — *Near-term*

**The idea.** Now that an agent's stored version refreshes from its metrics
payload (so it reflects the *currently running* build), add a fleet view that
flags agents whose `version` ≠ the latest released agent version — fleet-wide
update visibility ("which servers are behind?").

**Why deferred (lightly).** Small and self-contained; it simply hadn't been
needed until version reporting was trustworthy. It builds directly on the
version-refresh fix.

**Considerations.** Needs a source of truth for "latest version" (a constant, a
release feed, or an admin-set target). A simple version-comparison + a filter/
badge on the Servers list is the MVP; semantic-version comparison and a
per-agent "update available" indicator are the natural follow-ups.

---

## Agent auto-update — *Arc · gated (post-evaluation, strict security controls)*

**The idea.** Agents update themselves to a newer released version automatically,
over the existing **pull channel** (the ~30s metrics check-in already used for
desired-config delivery). It builds naturally on the version-tracking foundation
(agents report their version; the server knows the latest release) and the
"agents needing update" fleet view above — visibility precedes update.

**Why deferred — the critical considerations:**

- **This is the highest-RCE-risk feature in the product.** By definition it's a
  mechanism to make many remote, privileged (LocalSystem/root) machines download
  and execute new code automatically. If the update channel is compromised
  (server, release, or delivery path), an attacker could push malicious code to
  the **entire fleet at once** — turning the monitoring fleet into a botnet. The
  *security* of the update mechanism **is** the feature; the download-and-run part
  is trivial by comparison.
- **Non-negotiable — cryptographic binary verification.** The agent MUST verify a
  **signature** on the downloaded binary against a **pinned public key** (baked
  into the agent / its trust chain) **before executing it**. Code signing
  (cosign/sigstore, or an Ed25519 signature over the binary hash) is the gold
  standard; at minimum, checksum verification over the authenticated mTLS channel.
  An auto-updater that runs unverified binaries is a remote-code-execution
  feature, not a convenience.
- **Staged / opt-in rollout.** Operator-controlled, not auto-pull-on-every-release:
  canary a few agents, verify healthy, then roll to the fleet. A bad release must
  not break the whole fleet simultaneously.
- **Rollback safety.** If the new binary fails to start/enroll/report within N
  seconds, the agent must fall back to the previous version. Keep the prior binary
  and health-check before committing — bricking an agent with no recovery is worse
  than no auto-update, because the agent *is* the remote-access path (a dead agent
  can't be remotely fixed).
- **OS-specific swap mechanics.** A process replacing its own running binary:
  Windows can't overwrite a running `.exe` (download new → stop the service via the
  SCM → swap → restart); Linux is similar (replace file → restart via systemd).
  Atomic swap (temp → verify → rename), coordinated with the service supervisor.
- **Privilege.** The agent runs as LocalSystem/root (to manage services), so an
  auto-updater at that privilege fetching and executing code is a high-value
  target — the signature check is what keeps that privilege from being weaponized.
- **Eval timing.** Adding a self-updating RCE mechanism right before a security
  review is poor timing. Build it **post-eval**, and build it so the signing /
  staging / rollback make it a *strength* to demonstrate (a signed, staged,
  rollback-safe updater) rather than a risk to explain away.

**First step (already on this roadmap).** The "agents needing update" view —
fleet version *visibility* (compare each agent's reported version to the latest
release, flag stale ones) precedes fleet auto-*update*. The pull-config channel
(in progress) is the delivery substrate both would use.

**Scope.** A real feature with serious security design: signing infrastructure +
verification, staged-rollout control, atomic per-OS self-swap, rollback-on-failure
health-gating. Comparable to or larger than the Windows agent arc.

---

## MCP server (agent-accessible tools) — *Design pass*

**The idea.** Expose spane's capabilities — down devices, open CVEs, compliance
status, alert state — as **MCP tools** so external AI agents can query the
platform over a standard protocol. Think "ChatOps, but protocol-standard and
usable by any MCP-speaking agent," not just the built-in chat.

**Why it's tractable.** It's an **adapter over the existing API**, not new core
functionality, and it reuses the existing **RBAC / deny-by-default** model as the
safety substrate: read tools (status queries) can be liberal; action tools
(anything that changes state) stay capability-gated exactly as the HTTP API is.

**Considerations.** Authorization for a non-human caller (which identity/role
does an agent act as?); **prompt-injection** exposure when tool output is fed
back into an LLM; rate limiting; and keeping the tool surface a thin, audited
projection of the API rather than a parallel code path.

---

## Agentless server monitoring (SNMP) — *Design pass*

**The idea.** Monitor servers that **can't run the agent** — appliances, embedded
systems, third-party boxes — via basic SNMP and reachability, so they still
appear as servers with at least liveness and core metrics.

**Why deferred.** It needs its own design pass: a **manual-add server flow**
(distinct from agent enrollment), an SNMP polling path for server-shaped metrics,
and a decision on the **pinned-manual-IP vs discovery** question (the same
tension already handled for devices with `ip_locked`).

**Considerations.** Reconcile agentless servers with the agent-backed `Agent`/
`Device` model (an agentless server has no `Agent` row); decide how roles and
the online/degraded/offline state apply without a heartbeat; reuse the existing
SNMP collection stack rather than building a parallel one.

---

## Service checks from servers (agent as a check vantage point) — *Design pass*

**The idea.** Today a ServiceCheck runs from one or more **collectors**
(apps/checks: the `ServiceCheckCollector` through-table already models
multi-vantage-point execution — which collectors run a check, with resolution +
result aggregation). Let a check ALSO run **from an enrolled server/agent**,
making the agent an additional check vantage point.

**Why it's useful.** A collector probes from the network's vantage point (can the
collector reach port 443 on host X). Running the same check FROM a server tests
reachability from that server's vantage point — "can app-server-A actually reach
the database on db-server-B," "can this host reach an external dependency." That
catches host-local / east-west connectivity issues a central collector can't see
(firewall rules, routing, local DNS, per-host egress). It turns the agent fleet
into a distributed mesh of vantage points — closer to how the service is actually
experienced.

**Why deferred.** Needs a design pass:

- The agent does metrics + role-checks + (in progress) log-forwarding, all
  **OUTBOUND** POSTs. Agent-run checks mean the agent executes a probe
  (TCP/HTTP/port) against a target and POSTs the result — a new capability. Keep
  it outbound-only: run locally, POST the result, no inbound trigger
  (small-attack-surface model).
- **SECURITY.** A server-run check is an outbound connection to an
  operator-specified target — constrain it (don't turn the agent into an arbitrary
  network-probe / SSRF tool): allowlist / validate targets, RBAC-gate who can
  assign agent-run checks, audit. Mirror the `additional_paths` allowlist
  discipline from log-forwarding (validate both server- and agent-side).
- **Reuse the existing ServiceCheck model + multi-vantage aggregation** — extend
  "vantage point" from {collectors} to {collectors, agents} (a `ServiceCheckAgent`
  analog to the `ServiceCheckCollector` through-table) rather than a parallel
  system.
- **Config-driven** via the agent desired-config pull (dogfoods the config
  system): the agent learns which checks to run from its config.
- **Result aggregation.** A check from N collectors + M agents needs sensible
  multi-vantage rollup (extend the existing collector aggregation).

**Relationship to other work.** Builds on the agent config-pull (delivery of
which-checks-to-run) and mirrors the log-forwarding security discipline (target
allowlist, both-sides validation, audit).

---

## Synthetic / browser-based service monitoring — *Arc · gated (post-evaluation)*

**The idea.** For HTTP/HTTPS service checks, render the target in a **headless
browser** (Chromium via Playwright/Puppeteer) to (a) capture a **screenshot** of
the rendered page and (b) measure **real page-load timing** — TTFB,
DOM-content-loaded, full load, optionally Core Web Vitals. This is *synthetic
monitoring*: it goes beyond a status-code check. Visual confirmation catches the
"returns 200 but the page is broken" case; load timing is a real
user-experience measurement.

**Why deferred — the real considerations:**

- **Heavy new dependency.** A headless-Chromium worker (~300 MB plus system
  libraries) is a new fat service/container, not a code tweak.
- **Resource cost.** Rendering is hundreds of MB of RAM and *seconds* of CPU
  **per check** — versus microseconds for a TCP check. It needs concurrency
  limits and its own worker so it can't starve the platform, and it ties into the
  deferred capacity/GPU question.
- **Security — the critical one.** A browser fetching **operator-supplied URLs**
  is a powerful SSRF and code-execution surface: it can reach internal services,
  cloud metadata (`169.254.169.254`), `file://`, and intranet admin panels — a
  far richer attack surface than the existing `validate_outbound_url` /
  `urlopen` guard, and headless Chromium carries its own CVE stream. This would
  be the platform's **most security-sensitive component** and must be designed
  with a hard SSRF/egress boundary, sandboxing, and review — **not bolted on**.
  It is explicitly **deferred until after the security evaluation** for this
  reason.
- **Storage.** Screenshots are binary blobs (could reuse the `MEDIA_ROOT`
  pattern) and need a retention policy; load metrics are time-series (InfluxDB,
  like existing metrics).

**Scope.** A real feature arc — comparable to or bigger than the MFA work: a new
browser-worker service, a `browser`/`synthetic` check type in the checks model,
the SSRF/sandboxing security boundary, screenshot storage plus a load-metric
pipeline, and the display UI.

---

## Phishing-resistant authentication (passkeys / WebAuthn) — *Arc · upgrade, not a gap*

**The idea.** Add **passkeys / WebAuthn** as another authenticator alongside the
existing TOTP MFA — public-key rather than shared-secret, and **phishing-
resistant**.

**Why deferred.** This is a **security upgrade, not a gap**: TOTP MFA already
satisfies the multi-factor control, so passkeys raise the ceiling rather than
close a hole. The lift is also larger — WebAuthn registration/authentication
ceremonies, the browser credential API, and an attestation/recovery design for
device loss.

**Considerations.** The `MFADevice` model was deliberately structured so a second
authenticator type can extend it rather than requiring a redesign. Account-
recovery and device-loss flows are the hard part (and overlap with the existing
recovery-codes design).
