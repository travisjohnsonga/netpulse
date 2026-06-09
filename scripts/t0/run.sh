#!/usr/bin/env bash
# T0 substrate proof. Brings up the isolated harness and proves three facts:
#   1. leaf connects over TLS 1.3 + mTLS (and rejects TLS 1.2 / a missing client cert)
#   2. hub stream sources from edge stream; a leaf cut buffers at the edge and
#      replays on reconnect with NO loss and NO duplicates
#   3. a hub KV bucket watched from the edge resumes after a cut and converges to
#      the latest revision
# Leaves the harness UP for inspection. Tear down with: docker compose -f
# docker-compose.t0.yml down -v
set -uo pipefail
cd "$(dirname "$0")"
DC="docker compose -f docker-compose.t0.yml"

PASS=0; FAIL=0
ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
hub()  { $DC exec -T driver nats --server hub:4222 --user npuser --password nppass --js-domain hub "$@"; }
edge() { $DC exec -T driver nats --server edge:4222 "$@"; }
hub_msgs()  { $DC exec -T driver sh -c "nats --server hub:4222 --user npuser --password nppass --js-domain hub stream info HUB_TELEMETRY --json" | jq -r '.state.messages'; }
edge_msgs() { $DC exec -T driver sh -c "nats --server edge:4222 --js-domain edge stream info EDGE_TELEMETRY --json" | jq -r '.state.messages'; }
leaf_count(){ $DC exec -T driver sh -c "wget -qO- http://hub:8222/leafz" | jq -r '.leafnodes'; }

wait_leaf()    { for _ in $(seq 1 30); do [ "$(leaf_count)" = "1" ] && return 0; sleep 1; done; return 1; }
wait_no_leaf() { for _ in $(seq 1 30); do [ "$(leaf_count)" = "0" ] && return 0; sleep 1; done; return 1; }
wait_hub()     { local want=$1; for _ in $(seq 1 30); do [ "$(hub_msgs)" = "$want" ] && return 0; sleep 1; done; return 1; }

echo "== Bringing up T0 harness (fresh) =="
$DC down -v >/dev/null 2>&1
$DC up -d
echo "Waiting for leaf connection (TLS 1.3 mTLS)…"
if wait_leaf; then ok "leaf connected (hub /leafz reports 1)"; else bad "leaf never connected"; $DC logs --tail=40 edge; exit 1; fi

echo; echo "== 1. TLS 1.3 + mTLS on the leaf listener (host → leafproxy:17422 → hub:7422) =="
# The leaf is handshake_first (TLS-first), so openssl can negotiate it directly.
C="certs"
probe() { echo | openssl s_client -connect localhost:17422 -CAfile "$C/ca.pem" "$@" 2>/dev/null; }
# A: TLS 1.3 + valid client cert → must negotiate TLSv1.3 with a real cipher.
if probe -cert $C/edge-cert.pem -key $C/edge-key.pem -tls1_3 | grep -q "Cipher is TLS_"; then
  ok "TLS 1.3 + client cert → handshake OK ($(probe -cert $C/edge-cert.pem -key $C/edge-key.pem -tls1_3 | grep -oE 'TLS_[A-Z0-9_]+' | head -1))"
else bad "TLS 1.3 + client cert handshake"; fi
# B: force TLS 1.2 → hub's min_version 1.3 must refuse (no 1.2 cipher).
if probe -cert $C/edge-cert.pem -key $C/edge-key.pem -tls1_2 | grep -qE "Cipher is (TLS|ECDHE|AES)"; then
  bad "TLS 1.2 was accepted (min_version not enforced)"; else ok "TLS 1.2 rejected (min_version 1.3 enforced)"; fi
# C: mTLS required — a throwaway edge presenting NO client cert must be refused
# by the hub (verify:true), so the hub's leaf count stays at 1 (the legit edge).
NET="netpulse-t0_t0net"
docker run -d --name t0-nocert --network "$NET" \
  -v "$PWD/edge_nocert.conf:/c.conf:ro" -v "$PWD/certs:/certs:ro" \
  nats:2.10-alpine -c /c.conf >/dev/null 2>&1
sleep 7
NOCERT_LEAFZ=$(leaf_count)
NOCERT_LOG=$(docker logs t0-nocert 2>&1 | grep -iE "tls|handshake|certificate|bad|error" | tail -1)
docker rm -f t0-nocert >/dev/null 2>&1
[ "$NOCERT_LEAFZ" = "1" ] && ok "no-cert edge rejected — hub leaf count stayed 1 (mTLS required)" \
                          || bad "no-cert edge was accepted (leafz=$NOCERT_LEAFZ)"
echo "    no-cert edge log: ${NOCERT_LOG:-<none>}"

echo; echo "== 2. Telemetry: hub stream sources from edge stream; cut → buffer → replay =="
edge --js-domain edge stream add EDGE_TELEMETRY --subjects 't0.telemetry.>' --storage file --defaults >/dev/null 2>&1
hub stream add --config /work/hub_stream.json >/dev/null 2>&1
edge pub 't0.telemetry.x' 'pre-{{Count}}' --count 5 >/dev/null 2>&1
if wait_hub 5; then ok "5 edge messages sourced into the hub stream"; else bad "initial sourcing (hub has $(hub_msgs), want 5)"; fi

echo "  -- cutting the leaf (kill leafproxy; wait for the hub to detect it) --"
$DC kill leafproxy >/dev/null 2>&1
if wait_no_leaf; then echo "  leaf is DOWN (hub /leafz=0)"; else bad "leaf did not drop after cut"; fi
edge pub 't0.telemetry.x' 'cut-{{Count}}' --count 20 >/dev/null 2>&1
EC=$(edge_msgs); HC=$(hub_msgs)
echo "  while cut: edge stream=$EC, hub stream=$HC"
[ "$EC" = "25" ] && ok "edge buffered all 25 locally during the cut" || bad "edge buffer (edge=$EC, want 25)"
[ "$HC" = "5" ]  && ok "hub did NOT advance during the cut (still 5)" || bad "hub advanced during cut (hub=$HC)"

echo "  -- restoring the leaf (start leafproxy) --"
$DC start leafproxy >/dev/null 2>&1
wait_leaf >/dev/null
if wait_hub 25; then
  ok "after reconnect hub replayed to 25 — NO LOSS"
  [ "$(hub_msgs)" = "$(edge_msgs)" ] && ok "hub messages == edge messages (25) — NO DUPLICATES" || bad "dup/loss (hub=$(hub_msgs) edge=$(edge_msgs))"
else bad "replay did not reach 25 (hub=$(hub_msgs))"; fi

echo; echo "== 3. Config-down: hub KV watched from the edge; cut → resume → converge =="
hub kv add collector-config-1 --history=5 --storage=file >/dev/null 2>&1
$DC exec -d driver sh -c "nats --server edge:4222 --js-domain hub kv watch collector-config-1 > /tmp/watch.out 2>&1"
sleep 2
hub kv put collector-config-1 config 'rev=r1' >/dev/null 2>&1
sleep 3
$DC exec -T driver grep -q 'rev=r1' /tmp/watch.out && ok "edge watcher received r1 across the leaf" || bad "watcher did not receive r1"

echo "  -- cutting the leaf, writing r2/r3/r4 to the hub --"
$DC kill leafproxy >/dev/null 2>&1
wait_no_leaf >/dev/null && echo "  leaf is DOWN (hub /leafz=0)"
for r in r2 r3 r4; do hub kv put collector-config-1 config "rev=$r" >/dev/null 2>&1; done
sleep 2
if $DC exec -T driver grep -q 'rev=r4' /tmp/watch.out; then bad "watcher saw r4 while cut (impossible)"; else ok "watcher did NOT see r2/r3/r4 while cut"; fi

echo "  -- restoring the leaf --"
$DC start leafproxy >/dev/null 2>&1
wait_leaf >/dev/null
sleep 4
LATEST=$(edge --js-domain hub kv get collector-config-1 config --raw 2>/dev/null)
echo "  edge KV get (via leaf) after reconnect: '$LATEST'"
[ "$LATEST" = "rev=r4" ] && ok "edge converged to the latest revision (r4)" || bad "edge did not converge (got '$LATEST')"
$DC exec -T driver grep -q 'rev=r4' /tmp/watch.out && ok "edge watcher resumed and delivered r4" || bad "watcher did not resume to r4"

echo; echo "================= T0 RESULT: $PASS passed, $FAIL failed ================="
echo "watcher transcript:"; $DC exec -T driver cat /tmp/watch.out 2>/dev/null | sed 's/^/    /'
[ "$FAIL" = "0" ]
