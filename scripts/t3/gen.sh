#!/usr/bin/env bash
# Rotation harness identity: operator + signing key SK1, 16 collector accounts
# (+ AGG) all minted with -K SK1 (Finding A). The operator JWT is split into a
# standalone file `state/operator.jwt` (the deploy-pipeline artifact a human
# rewrites + SIGHUPs — Finding B Option 2); the resolver block (no operator line)
# is a separate include.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf nsc creds out state && mkdir -p nsc creds out state
IMG=natsio/nats-box:latest
NSC=( docker run --rm -v "$PWD/nsc:/nsc" -v "$PWD/creds:/creds" -v "$PWD/out:/out"
      -e NSC_HOME=/nsc -e XDG_DATA_HOME=/nsc/data -e XDG_CONFIG_HOME=/nsc/config
      -e NKEYS_PATH=/nsc/keys "$IMG" )
run(){ "${NSC[@]}" sh -lc "$1"; }
run "set -e
  nsc add operator --name NetPulseRot --sys >/dev/null
  nsc edit operator --sk generate >/dev/null
  nsc edit operator --account-jwt-server-url nats://colhub:4222 >/dev/null
  nsc add user -a SYS sys >/dev/null 2>&1 || true
  nsc generate creds -a SYS -n sys > /creds/SYS.creds
  SK=\$(nsc describe operator -J | jq -r '.nats.signing_keys[0]')
  echo \$SK > /out/SK1
  nsc add account AGG -K \$SK >/dev/null
  nsc add user -a AGG bridge >/dev/null
  nsc generate creds -a AGG -n bridge > /creds/AGG.creds
  for i in \$(seq 1 16); do
    nsc add account COLL\$i -K \$SK >/dev/null
    nsc add user -a COLL\$i agent >/dev/null
    nsc generate creds -a COLL\$i -n agent > /creds/COLL\$i.creds
  done
  nsc generate config --nats-resolver > /out/full.conf
  echo \"minted 16 collectors + AGG, all iss=SK1=\$SK\""
# Split: operator JWT → state/operator.jwt (deploy artifact); rest → resolver include.
grep '^operator:' out/full.conf | sed 's/^operator: //' > state/operator.jwt
grep -v '^operator:' out/full.conf | sed 's#dir: .*#dir: "/data/jwt"#; s/allow_delete: false/allow_delete: true/' > out/resolver_rot.conf
echo "SK1=$(cat out/SK1)"
