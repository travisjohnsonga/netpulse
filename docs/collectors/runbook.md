# Remote-collector operations runbook

Operator steps for the remote-collector subsystem. **Only the parts that are real
today** are documented here. Background: `docs/ARCHITECTURE.md` §10. Blocking
gates: `docs/collector-production-gates.md`. Proof harnesses: `scripts/t0`–
`scripts/t3`.

Maturity: **[VALIDATED]** proven end-to-end · **[BUILT]** committed, not yet
proven end-to-end · **[PLANNED]** not built.

> ⚠️ The on-edge **collector agent** and the `setup.sh` **Collector** role are
> **[PLANNED] — not built**. Do not follow agent-side install steps; they don't
> exist yet. See "TODO (not built)" at the bottom.

---

## 1. Enroll a collector  [BUILT central side · VALIDATED bus mechanic]

Two layers per collector — they are separate on purpose (cert = transport
identity, account creds = bus identity):

**a) Central record + API key + PKI cert** (`apps/collectors`, committed):
- Create the `Collector` row → returns a **one-time enrollment token** (the stored
  `api_key_hash` is a `pending-…` sentinel until enrollment).
- The agent exchanges the token **once** for its API key (bcrypt-hashed at rest) +
  a NATS account name + a best-effort per-collector PKI client cert from the
  OpenBao intermediate (`pki_int`, role `collector`). Provision the PKI once with
  `manage.py setup_collector_pki` (idempotent).

**b) Bus identity — mint + push the operator-signed account** (the mechanic proven
in `scripts/t1`/`scripts/t2`, run where `nsc` + the operator signing key live):
```bash
# SK = the operator SIGNING key (never the identity key — see rotation).
nsc add account  COLL<id> -K $SK          # MUST pass -K $SK, or nsc signs with the
                                          # cold identity key (scripts/t3 finding)
nsc add user     -a COLL<id> agent
nsc generate creds -a COLL<id> -n agent > COLL<id>.creds   # → the agent's .creds
nsc push --all -u nats://<collector-hub>:4222              # runtime, NO nats.conf reload
```
The collector then dials the collector-hub leaf listener with its **cert**
(transport) + **.creds** (bus). Resolver is `full`/local-store, so a reconnecting
collector resolves from the hub's local store — resolver availability is out of
the steady-state connect path.

## 2. Revoke a collector  [VALIDATED]

Runtime, no server restart (proven in `scripts/t1`: the evicted leaf drops, others
unaffected, hub `StartedAt` unchanged):
```bash
nsc delete account COLL<id> --force
nsc push --all --prune -u nats://<collector-hub>:4222
```
Also revoke the central record (rotate/clear its API key) and let the PKI cert
expire / revoke it at the intermediate.

## 3. Rotate the operator signing key  [VALIDATED — both modes]

The operator **signing key** signs all collector accounts; the operator
**identity key** is kept **cold/offline**. Rotation changes the operator JWT,
which the collector-hub picks up on a **connection-preserving SIGHUP** (not a
restart). Account add/revoke stays reload-free; only this rare operation needs the
SIGHUP.

`manage.py rotate_operator_signing_key --mode {planned|compromise}` **prepares the
runbook artifacts** (the account set to re-sign + the ordered steps). It does NOT
execute: minting a signing key re-signs the operator JWT with the **cold identity
key**, so a human/secure pipeline runs `nsc` + drives the SIGHUP fan-out. The
operator JWT is a deploy-pipeline artifact, never rewritten by the live API.

- **Planned (zero breakage)** — re-sign + push **before** retiring the old key:
  ```
  nsc edit operator --sk generate                 # mint SK_new (signs op JWT w/ IDENTITY key)
  <deploy>: rewrite operator.jwt on every hub + SIGHUP   # trust SK_old + SK_new
  for A in <accounts>; do nsc edit account $A -K SK_new; done
  nsc push --all -u nats://<hub>:4222             # push BEFORE retiring SK_old → 0 breakage
  nsc edit operator --rm-sk SK_old
  <deploy>: rewrite operator.jwt + SIGHUP         # retire SK_old
  ```
- **Compromise (bounded breakage)** — stage all re-signed JWTs **first**, then cut
  over, so the window is **push-bounded** (`scripts/t3` measured ~2.4s for 16
  accounts), not push+sign:
  ```
  nsc edit operator --sk generate
  for A in <accounts>; do nsc edit account $A -K SK_new; done   # STAGE: re-sign, do NOT push
  nsc edit operator --rm-sk SK_old
  <deploy>: rewrite operator.jwt (SK_new only) + SIGHUP   # cutover; SK_old JWTs now invalid
  nsc push --all -u nats://<hub>:4222                     # push the prepared batch → recovery
  ```

---

## BLOCKING GATES — must pass before any collector handles real credentials in prod

Mirrors `docs/collector-production-gates.md`. Do not let a collector touch real
device creds until every box is checked.

- [ ] **Identity-from-transport proven end-to-end** — a client on account A's
  creds, over the **real transport**, is refused account B's device creds
  (A-can't-fetch-B). The broker authorization logic is VALIDATED in unit tests,
  but the cross-account NATS routing that conveys the caller's account is **[BUILT]
  — routing still open**; this gate is **NOT yet met**.
- [ ] **Broker fails closed in prod without its AppRole** — `check_broker_config()`
  refuses to start and `_scoped_read` refuses the platform-reader fallback when
  `BROKER_REQUIRE_APPROLE` (default `not DEBUG`) is set without the scoped AppRole.
  **[VALIDATED]** (tests).
- [ ] **Collector-hub leaf listener hardened** — `advertise: <fixed-ingress>` +
  `no_advertise: true` + `handshake_first: true` (+ `verify: true`,
  `min_version: "1.3"`). **[VALIDATED]** (scripts/t1 hub.conf).
- [ ] **Broker AppRole policy is read-only / no-list** — `read` on
  `secret/data/netpulse/credentials/+`, **no list anywhere**, nothing broader;
  verified against the **live** OpenBao (read ✓, list 403, out-of-scope 403).
  **[VALIDATED].** Re-verify after any policy edit.

---

## TODO (not built — do not document as runnable)  [PLANNED]

- **Collector agent operation** (`services/collector`): the forwarding process,
  local edge JetStream buffer/replay, and broker client. Not built — no install,
  start, or troubleshooting steps exist yet.
- **`setup.sh` Collector role / `docker-compose.collector.yml`**: not built.
- **api rebuild for migration 0004** (collector identity fields): committed but not
  applied on running stacks until the next api rebuild.
