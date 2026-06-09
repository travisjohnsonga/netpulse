# T4 — secret-broker identity-from-transport harness (WIP)

Goal: prove a collector connected on account A's creds, over the real transport,
is refused B's device creds (A-can't-fetch-B end-to-end).

Status (checkpoint): the broker authorization (34 unit tests), the least-privilege
OpenBao policy (read-yes / list-no / out-of-scope-no, verified on the live vault),
the prod fail-closed gate, and the `run_secret_broker` entrypoint (identity from
the NATS-injected subject, never the body) are all proven.

OPEN — transport identity wiring: the cross-account service routing that conveys
the caller's account to the broker is NOT yet landed.
  * Plain single-token cross-account service export/import ROUTES (verified).
  * `account_token_position` (canonical) did not route after 3 focused attempts
    (export subject / import remap / token position were checked and aligned).
  * A per-account import subject (multi-token) also did not route in this harness.
The blocker is a NATS service-export/import wiring mechanic, NOT a hole in the
broker logic or policy. Whichever mechanism wins, the identity signal MUST be
NATS-attached-and-verified, never read from the message body.

See ../../docs/collector-production-gates.md — this is a BLOCKING gate.

Reproduce: ./gen.sh then bring up docker-compose.echo.yml and poke the service.
