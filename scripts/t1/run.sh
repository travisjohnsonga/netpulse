#!/usr/bin/env bash
# T1a proof: operator/JWT identity + mTLS transport on one leaf.
#   - both collector leaves connect with operator-signed creds (bus id) + mTLS
#     cert (transport id); each binds to its OWN account
#   - an untrusted client cert is refused (proven by leaf-close + /leafz NOT
#     incrementing — the correct signal; TLS 1.3 mTLS is post-handshake)
#   - account isolation: COLL1 traffic is invisible to COLL2
#   - revoke a collector at RUNTIME (delete its account JWT) → its leaf is
#     evicted, the other stays, the hub is NEVER restarted (no nats.conf reload)
set -uo pipefail
cd "$(dirname "$0")"
DC="docker compose -f docker-compose.t1.yml"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
dex(){ $DC exec -T driver "$@"; }
leafz(){ dex sh -c "wget -qO- http://hub:8222/leafz"; }
leafn(){ leafz | jq -r '.leafnodes'; }
wait_leafn(){ local want=$1; for _ in $(seq 1 40); do [ "$(leafn)" = "$want" ] && return 0; sleep 1; done; return 1; }
started_at(){ docker inspect -f '{{.State.StartedAt}}' netpulse-t1-hub-1 2>/dev/null; }

echo "== Bring up T1a (operator/JWT + mTLS leaf) =="
$DC down -v >/dev/null 2>&1
$DC up -d
sleep 4

echo "== Mint→push: register the two collector accounts in the hub resolver (runtime, NO reload) =="
dex sh -lc "nsc push --all -u nats://hub:4222" 2>&1 | sed 's/^/    /' | tail -5

echo; echo "== 1. JWT identity: both collector leaves connect (creds + mTLS cert) =="
if wait_leafn 2; then ok "2 collector leaves connected — each its own operator-signed account"; else bad "leaves did not connect (leafz=$(leafn))"; $DC logs --tail=15 edge1 2>&1 | tail -6; fi
echo "  leaf accounts seen by the hub:"; leafz | jq -r '.leafs[]? | "    account="+.account' 2>/dev/null | sort -u

echo; echo "== 2. TRANSPORT negative: untrusted client cert refused (mTLS) =="
BEFORE=$(leafn)
docker run -d --name t1-edgebad --network netpulse-t1_t1net \
  -v "$PWD/edgebad.conf:/etc/nats/edge.conf:ro" -v "$PWD/certs:/certs:ro" -v "$PWD/creds:/creds:ro" \
  nats:2.10-alpine -c /etc/nats/edge.conf >/dev/null 2>&1
sleep 7
AFTER=$(leafn)
BADLOG=$(docker logs t1-edgebad 2>&1 | grep -iE "tls|handshake|certificate|error|bad" | tail -1)
docker rm -f t1-edgebad >/dev/null 2>&1
[ "$AFTER" = "$BEFORE" ] && ok "untrusted-cert leaf refused — hub leaf count unchanged ($BEFORE)  [signal: leaf close + /leafz]" \
                         || bad "untrusted-cert leaf was ACCEPTED (before=$BEFORE after=$AFTER)"
echo "    edgebad log: ${BADLOG:-<none>}"

echo; echo "== 3. ISOLATION: COLL1 traffic is invisible to COLL2 =="
dex sh -lc "timeout 6 nats --server edge2:4222 sub 'iso.>' >/tmp/iso2.out 2>&1 &" >/dev/null 2>&1
dex sh -lc "timeout 6 nats --server edge1:4222 sub 'iso.>' >/tmp/iso1.out 2>&1 &" >/dev/null 2>&1
sleep 2
dex sh -lc "nats --server edge1:4222 pub iso.test hello" >/dev/null 2>&1
sleep 3
dex grep -q hello /tmp/iso1.out && ok "same-account delivery works (edge1 sub saw it)" || bad "same-account delivery failed"
dex grep -q hello /tmp/iso2.out && bad "CROSS-account LEAK (edge2/COLL2 saw COLL1 traffic)" || ok "cross-account isolation holds (edge2 saw nothing)"

echo; echo "== 4. REVOKE at runtime (no nats.conf reload): delete COLL1 account =="
START_BEFORE=$(started_at)
dex sh -lc "nsc delete account COLL1 --force 2>&1 | tail -1; nsc push --all --prune -u nats://hub:4222 2>&1 | tail -2" | sed 's/^/    /'
if wait_leafn 1; then ok "COLL1 leaf evicted at runtime — leaf count dropped to 1"; else bad "COLL1 not evicted (leafz=$(leafn))"; fi
ACC_LEFT=$(leafz | jq -r '.leafs[0]?.account' 2>/dev/null)
[ -n "$ACC_LEFT" ] && [ "$ACC_LEFT" != "null" ] && ok "COLL2 leaf still connected (account=$ACC_LEFT)" || bad "COLL2 also dropped"
START_AFTER=$(started_at)
[ "$START_BEFORE" = "$START_AFTER" ] && ok "hub NOT restarted (resolver update, not a config reload): StartedAt unchanged" \
                                     || bad "hub restarted (StartedAt changed)"

echo; echo "================= T1a RESULT: $PASS passed, $FAIL failed ================="
[ "$FAIL" = "0" ]
