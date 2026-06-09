# Remote-collector production gates (BLOCKING)

These MUST pass before any remote collector handles real credentials in
production. They are gates, not aspirations — "defer" must not become "forget".

## GATE 1 — identity-from-transport proven end-to-end  ❌ NOT YET MET

A client connected on account A's credentials, over the REAL transport (mTLS
leaf + the broker's cross-account service), asks the secret-broker for one of
account B's devices → it MUST be refused (`forbidden`). This is the linchpin of
the confused-deputy guarantee.

Proven so far: the broker authorization logic (unit tests incl. the
confused-deputy case), the least-privilege OpenBao policy (read-only, no list,
verified on the live vault), the prod fail-closed gate, and the entrypoint
deriving identity from the NATS-injected subject (never the body).

NOT yet proven: the cross-account NATS service routing that conveys the caller's
account to the broker (see scripts/t4/README.md). The identity signal, whatever
mechanism lands, MUST be NATS-attached-and-verified — never anything in the
message body. The end-to-end harness may be completed when the agent + real leaf
exist (it needs the real leaf), but THIS GATE blocks production credential
handling until A-can't-fetch-B is demonstrated over the real transport.

## GATE 2 — broker runs under its least-privilege AppRole, never root  ✅ enforced

`check_broker_config()` refuses to start in production without the scoped AppRole;
`_scoped_read` refuses the platform-reader fallback in prod. Policy verified:
read on secret/data/netpulse/credentials/+, NO list, nothing broader.
