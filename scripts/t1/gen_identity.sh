#!/usr/bin/env bash
# T1 identity material — the operator/JWT trust hierarchy (BUS identity) +
# per-collector account creds. Runs nsc inside nats-box so the host needs no
# tooling. Throwaway — the real operator signing key lives in OpenBao only.
#
#   operator (NetPulseT1)
#     ├─ signing key (signs account JWTs; the identity key stays cold)
#     ├─ SYS account            (system account; used to push to the resolver)
#     └─ per-collector accounts: COLL1, COLL2  (isolation boundary, tenant-ready)
#          └─ user "agent"      → .creds the collector connects with
set -euo pipefail
cd "$(dirname "$0")"
rm -rf nsc creds && mkdir -p nsc creds out

IMG=natsio/nats-box:latest
NSC=( docker run --rm -v "$PWD/nsc:/nsc" -v "$PWD/creds:/creds" -v "$PWD/out:/out"
      -e NSC_HOME=/nsc -e XDG_DATA_HOME=/nsc/data -e XDG_CONFIG_HOME=/nsc/config
      -e NKEYS_PATH=/nsc/keys "$IMG" )

run() { "${NSC[@]}" sh -lc "$1"; }

# Operator + system account, with a dedicated signing key (so the operator
# identity key can stay offline and be rotated without re-minting accounts).
run "nsc add operator --name NetPulseT1 --sys >/dev/null
     nsc edit operator --sk generate >/dev/null
     # Tell the operator where the account-JWT resolver server is (for push).
     nsc edit operator --account-jwt-server-url nats://hub:4222 >/dev/null
     echo 'operator + SYS created'"

# Two collector accounts (each its own isolation boundary) + an agent user each.
for c in COLL1 COLL2; do
  run "nsc add account $c >/dev/null
       nsc edit account $c --sk generate >/dev/null
       nsc add user -a $c agent >/dev/null
       nsc generate creds -a $c -n agent > /creds/${c}.creds
       echo '$c account + agent creds minted'"
done

# Server-side artifacts: the nats-resolver config snippet (operator JWT +
# system_account + a full NATS-based resolver → accounts can be added/revoked at
# runtime by pushing JWTs, NO nats.conf reload).
run "nsc generate config --nats-resolver > /out/resolver.conf
     echo 'resolver.conf generated'"

# SYS user creds (used by run.sh to push/revoke accounts against the running hub).
run "nsc generate creds -a SYS -n sys > /creds/SYS.creds 2>/dev/null || \
     nsc add user -a SYS sys >/dev/null && nsc generate creds -a SYS -n sys > /creds/SYS.creds
     echo 'SYS push creds ready'"

# Make the resolver store dir absolute (writable volume) and allow runtime
# account delete (for the revoke-without-reload test).
sed -i 's#dir: .*#dir: "/data/jwt"#; s/allow_delete: false/allow_delete: true/' out/resolver.conf

echo '--- creds ---'; ls -1 creds
echo '--- resolver.conf (operator/JWT server setup) ---'; sed -n '1,20p' out/resolver.conf
