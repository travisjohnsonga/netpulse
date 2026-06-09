#!/usr/bin/env bash
# T2 proof: inter-NATS account-mapped telemetry + cut/replay.
#   COLL1 (collector account) ─pub→ export ─→ AGG import ─→ AGG.HUB_TELEMETRY (JS
#   buffer on the collector-hub) ─source→ internal.TELEMETRY ─→ stream-processor
#   durable consumer (sp-telemetry) on the UNTOUCHED internal bus.
# Cut the inter-NATS link, publish into the collector account while cut, reconnect,
# prove every message lands at the internal consumer with NO loss and NO dupes.
set -uo pipefail
cd "$(dirname "$0")"
DC="docker compose -f docker-compose.t2.yml"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
dex(){ $DC exec -T driver sh -lc "$1"; }
coll_pub(){ dex "nats --server colhub:4222 --creds /creds/COLL1.creds pub 'netpulse.telemetry.dev1.metrics' '$1' --count $2" >/dev/null 2>&1; }
agg_msgs(){ dex "nats --server colhub:4222 --creds /creds/AGG.creds --js-domain colhub stream info HUB_TELEMETRY --json" | jq -r '.state.messages'; }
int_msgs(){ dex "nats --server internal:4222 --user npuser --password nppass --js-domain internal stream info TELEMETRY --json" | jq -r '.state.messages'; }
con_pending(){ dex "nats --server internal:4222 --user npuser --password nppass --js-domain internal consumer info TELEMETRY sp-telemetry --json" | jq -r '.num_pending'; }
leafn(){ dex "wget -qO- http://colhub:8222/leafz" | jq -r '.leafnodes'; }
wait_leaf(){ for _ in $(seq 1 40); do [ "$(leafn)" = "1" ] && return 0; sleep 1; done; return 1; }
wait_no_leaf(){ for _ in $(seq 1 40); do [ "$(leafn)" = "0" ] && return 0; sleep 1; done; return 1; }
wait_int(){ local w=$1; for _ in $(seq 1 40); do [ "$(int_msgs)" = "$w" ] && return 0; sleep 1; done; return 1; }

echo "== Bring up T2 + register accounts (runtime) =="
$DC down -v >/dev/null 2>&1; $DC up -d >/dev/null 2>&1; sleep 5
dex "nsc push --all -u nats://colhub:4222" >/dev/null 2>&1
dex "nats --server colhub:4222 --creds /creds/AGG.creds --js-domain colhub stream add HUB_TELEMETRY --subjects 'netpulse.telemetry.>' --storage file --defaults" >/dev/null 2>&1
dex "nats --server internal:4222 --user npuser --password nppass --js-domain internal stream add TELEMETRY --config /work/internal_telemetry.json" >/dev/null 2>&1
# stream-processor's durable consumer on the internal bus.
dex "nats --server internal:4222 --user npuser --password nppass --js-domain internal consumer add TELEMETRY sp-telemetry --pull --deliver all --ack explicit --max-deliver=-1 --defaults" >/dev/null 2>&1
wait_leaf >/dev/null

echo; echo "== 1. account-mapping: collector-account telemetry reaches the internal consumer =="
coll_pub 'pre-{{Count}}' 5
if wait_int 5; then ok "5 msgs: COLL1 account → AGG buffer → internal.TELEMETRY (sourced)"; else bad "mapping (internal=$(int_msgs))"; fi
[ "$(con_pending)" = "5" ] && ok "stream-processor consumer (sp-telemetry) has all 5 pending" || bad "consumer pending=$(con_pending)"

echo; echo "== 2. cut the inter-NATS link; publish into the collector account while cut =="
$DC kill interproxy >/dev/null 2>&1
if wait_no_leaf; then echo "  inter-NATS link DOWN (colhub /leafz=0)"; else bad "link did not drop"; fi
coll_pub 'cut-{{Count}}' 20
echo "  while cut: AGG buffer=$(agg_msgs)  internal.TELEMETRY=$(int_msgs)"
[ "$(agg_msgs)" = "25" ] && ok "collector-hub buffered all 25 (AGG.HUB_TELEMETRY)" || bad "hub buffer=$(agg_msgs)"
[ "$(int_msgs)" = "5" ]  && ok "internal bus did NOT advance during the cut (still 5)" || bad "internal advanced during cut=$(int_msgs)"

echo; echo "== 3. reconnect; prove no loss / no dupes at the internal consumer =="
$DC start interproxy >/dev/null 2>&1
wait_leaf >/dev/null
if wait_int 25; then
  ok "after reconnect internal.TELEMETRY replayed to 25 — NO LOSS"
  [ "$(int_msgs)" = "$(agg_msgs)" ] && ok "internal == hub buffer (25) — NO DUPLICATES" || bad "dup/loss (int=$(int_msgs) agg=$(agg_msgs))"
  [ "$(con_pending)" = "25" ] && ok "stream-processor consumer has all 25 pending (delivered end-to-end)" || bad "consumer pending=$(con_pending)"
else bad "replay did not reach 25 (internal=$(int_msgs))"; fi

echo; echo "================= T2 RESULT: $PASS passed, $FAIL failed ================="
[ "$FAIL" = "0" ]
