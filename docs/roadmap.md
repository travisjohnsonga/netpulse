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
be true while its actual **metrics pushes are failing** — observed in the lab:
`netmagic` showed *Online* for ~2 days while every metrics `POST` returned 502
during rebuilds. A monitoring tool should never read "fine" when it isn't
actually collecting.

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
