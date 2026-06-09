#!/usr/bin/env bash
# T4 identity: operator + BROKER account (exports the secrets service with
# account_token_position so the responder learns the CALLER's account from the
# subject) + COLL_A / COLL_B accounts that import it. Two collectors so we can
# prove A cannot fetch B's device creds over the real transport.
set -euo pipefail
cd "$(dirname "$0")"
rm -rf nsc creds out && mkdir -p nsc creds out
IMG=natsio/nats-box:latest
NSC=( docker run --rm -v "$PWD/nsc:/nsc" -v "$PWD/creds:/creds" -v "$PWD/out:/out"
      -e NSC_HOME=/nsc -e XDG_DATA_HOME=/nsc/data -e XDG_CONFIG_HOME=/nsc/config -e NKEYS_PATH=/nsc/keys "$IMG" )
run(){ "${NSC[@]}" sh -lc "$1"; }
run "set -e
  nsc add operator --name NetPulseT4 --sys >/dev/null
  nsc edit operator --sk generate >/dev/null
  nsc edit operator --account-jwt-server-url nats://hub:4222 >/dev/null
  nsc add user -a SYS sys >/dev/null 2>&1 || true; nsc generate creds -a SYS -n sys > /creds/SYS.creds
  SK=\$(nsc describe operator -J | jq -r '.nats.signing_keys[0]')
  # BROKER account: exports the secrets service; the * at token 4 is injected
  # with the importing account's id.
  nsc add account BROKER -K \$SK >/dev/null
  nsc add export -a BROKER --service --subject 'netpulse.secrets.fetch.*' --name secrets --account-token-position 4 >/dev/null
  nsc add user -a BROKER broker >/dev/null; nsc generate creds -a BROKER -n broker > /creds/BROKER.creds
  BROKERPUB=\$(nsc describe account BROKER -J | jq -r '.sub')
  for C in A B; do
    nsc add account COLL_\$C -K \$SK >/dev/null
    nsc add import -a COLL_\$C --src-account \$BROKERPUB --remote-subject 'netpulse.secrets.fetch.*' --service >/dev/null
    nsc add user -a COLL_\$C agent >/dev/null; nsc generate creds -a COLL_\$C -n agent > /creds/COLL_\$C.creds
    PUB=\$(nsc describe account COLL_\$C -J | jq -r '.sub'); echo \$PUB > /out/COLL_\$C.pub
  done
  nsc generate config --nats-resolver > /out/resolver.conf
  echo \"BROKER=\$BROKERPUB  A=\$(cat /out/COLL_A.pub)  B=\$(cat /out/COLL_B.pub)\""
sed -i 's#dir: .*#dir: "/data/jwt"#; s/allow_delete: false/allow_delete: true/' out/resolver.conf
echo "A_PUB=$(cat out/COLL_A.pub)"; echo "B_PUB=$(cat out/COLL_B.pub)"
