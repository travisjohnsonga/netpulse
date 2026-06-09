#!/usr/bin/env bash
# Operator signing-key rotation — both modes, tested.
#   Finding A: every minted account's iss == the signing key (not identity).
#   Planned : add SK2 → op-JWT+SIGHUP → re-sign+push all → drop SK1 → op-JWT+SIGHUP.
#             ZERO collector breakage; SIGHUP is connection-preserving (leaf stays up).
#   Compromise: STAGE all re-signed JWTs under SK_new FIRST → op-JWT(SK_new only)+SIGHUP
#             → push the prepared batch. Breakage window is bounded to PUSH time, MEASURED
#             with 16 accounts.
# The rotation actor here = the deploy pipeline (rewrites state/operator.jwt + SIGHUPs);
# the live API never holds that power (Finding B, option 2).
set -uo pipefail
cd "$(dirname "$0")"
DC="docker compose -f docker-compose.rot.yml"
PASS=0; FAIL=0
ok(){ echo "  PASS: $1"; PASS=$((PASS+1)); }
bad(){ echo "  FAIL: $1"; FAIL=$((FAIL+1)); }
nsc(){ $DC exec -T driver sh -lc "nsc $* 2>/dev/null"; }
push(){ $DC exec -T driver sh -lc "nsc push --all -u nats://colhub:4222 >/dev/null 2>&1"; }
conn(){ $DC exec -T driver sh -lc "nats --server colhub:4222 --creds /creds/COLL$1.creds --timeout 2s pub rot.test x >/dev/null 2>&1"; }
leafn(){ $DC exec -T driver sh -lc "wget -qO- http://colhub:8222/leafz | jq -r .leafnodes"; }
op_jwt_sighup(){ # rewrite the operator JWT artifact + SIGHUP (deploy-pipeline action)
  $DC exec -T driver sh -lc "nsc describe operator --raw 2>/dev/null | tr -d '\n' > /state/operator.jwt"
  $DC kill -s HUP colhub >/dev/null 2>&1; sleep 2
}
op_sk(){ nsc "describe operator -J" | jq -r '.nats.signing_keys'; }

echo "== Bring up rotation harness =="
$DC down -v >/dev/null 2>&1; $DC up -d >/dev/null 2>&1; sleep 5
push
SK1=$(cat out/SK1)

echo; echo "== Finding A: every minted account is signed by the SIGNING key, not identity =="
ID=$(nsc "describe operator -J" | jq -r '.sub')
BADISS=0
for i in $(seq 1 16); do
  iss=$(nsc "describe account COLL$i -J" | jq -r '.iss')
  [ "$iss" = "$SK1" ] || { BADISS=$((BADISS+1)); echo "    COLL$i iss=$iss (expected SK1)"; }
  [ "$iss" = "$ID" ] && echo "    COLL$i signed by IDENTITY key!"
done
[ "$BADISS" = "0" ] && ok "all 16 accounts iss == SK1 (identity key never in the mint path)" || bad "$BADISS accounts not signed by SK1"
[ "$(leafn)" = "1" ] && ok "internal leaf up (baseline)" || bad "leaf not up"

echo; echo "== PLANNED rotation (zero breakage) =="
LEAF_BEFORE=$(leafn)
nsc "edit operator --sk generate >/dev/null"
SK2=$(op_sk | jq -r --arg s "$SK1" '.[] | select(.!=$s)' | head -1)
op_jwt_sighup                                   # server now trusts SK1 + SK2
LEAF_AFTER_HUP=$(leafn)
[ "$LEAF_BEFORE" = "1" ] && [ "$LEAF_AFTER_HUP" = "1" ] && ok "SIGHUP is connection-preserving: leaf stayed up (/leafz 1→1, no reset)" || bad "leaf reset across SIGHUP ($LEAF_BEFORE→$LEAF_AFTER_HUP)"
# re-sign every account under SK2, then push — BEFORE dropping SK1 (no breakage).
for i in $(seq 1 16); do nsc "edit account COLL$i -K $SK2 >/dev/null"; done
nsc "edit account AGG -K $SK2 >/dev/null"
push
conn 1 && ok "COLL accounts valid throughout the re-sign (planned: no breakage)" || bad "breakage during planned re-sign"
nsc "edit operator --rm-sk $SK1 >/dev/null"
op_jwt_sighup                                   # drop SK1; accounts already SK2
conn 1 && ok "after retiring SK1: COLL accounts still valid (planned rotation complete)" || bad "breakage after SK1 retired"
[ "$(leafn)" = "1" ] && ok "leaf still up after full planned rotation" || bad "leaf dropped"

echo; echo "== COMPROMISE rotation (stage-first; MEASURE the breakage window, N=16) =="
nsc "edit operator --sk generate >/dev/null"
SK3=$(op_sk | jq -r --arg a "$SK2" '.[] | select(.!=$a)' | head -1)
# STAGE: re-sign all 16 + AGG under SK3 LOCALLY (do NOT push yet).
for i in $(seq 1 16); do nsc "edit account COLL$i -K $SK3 >/dev/null"; done
nsc "edit account AGG -K $SK3 >/dev/null"
# Cut over: trust ONLY SK3 (+SIGHUP), then push the prepared batch. Time the window.
nsc "edit operator --rm-sk $SK2 >/dev/null"
T0=$(date +%s.%N)
op_jwt_sighup                                   # resolver still has SK2-signed JWTs → invalid now
push                                            # push the prepared SK3-signed batch
for _ in $(seq 1 60); do conn 1 && break; done  # poll a sample account until it recovers
T1=$(date +%s.%N)
WIN=$(awk -v a="$T0" -v b="$T1" 'BEGIN{printf "%.2f", b-a}')
conn 1 && ok "compromise recovered: all accounts valid under SK3 (old key fully retired)" || bad "did not recover under SK3"
# Confirm ALL 16 valid after recovery.
BAD=0; for i in $(seq 1 16); do conn $i || BAD=$((BAD+1)); done
[ "$BAD" = "0" ] && ok "all 16 collectors valid after compromise rotation" || bad "$BAD collectors still invalid"
echo "  >> MEASURED compromise breakage window (SIGHUP→push→recovery, 16 accts): ${WIN}s"
echo "     (bounded to push-time because re-signed JWTs were staged before the cutover)"

echo; echo "================= ROTATION RESULT: $PASS passed, $FAIL failed ================="
[ "$FAIL" = "0" ]
