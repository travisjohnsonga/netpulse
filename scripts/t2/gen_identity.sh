#!/usr/bin/env bash
# T2 identity — operator/JWT hierarchy for the inter-NATS proof.
#   operator
#    ├─ SYS                (system account; push to resolver)
#    ├─ COLL1              (a collector account — isolation boundary)
#    │    ├─ user agent    (publishes telemetry)
#    │    └─ EXPORT stream "netpulse.telemetry.>"   (offers its telemetry)
#    └─ AGG               (aggregate account — the funnel to the internal bus)
#         ├─ JetStream enabled (the buffer that survives an inter-NATS cut)
#         ├─ IMPORT of COLL1's telemetry export   (per-collector; added at enroll)
#         └─ user bridge   (creds the inter-NATS leaf uses)
set -euo pipefail
cd "$(dirname "$0")"
rm -rf nsc creds out && mkdir -p nsc creds out
IMG=natsio/nats-box:latest
NSC=( docker run --rm -v "$PWD/nsc:/nsc" -v "$PWD/creds:/creds" -v "$PWD/out:/out"
      -e NSC_HOME=/nsc -e XDG_DATA_HOME=/nsc/data -e XDG_CONFIG_HOME=/nsc/config
      -e NKEYS_PATH=/nsc/keys "$IMG" )
run(){ "${NSC[@]}" sh -lc "$1"; }

run "nsc add operator --name NetPulseT2 --sys >/dev/null
     nsc edit operator --sk generate >/dev/null
     nsc edit operator --account-jwt-server-url nats://colhub:4222 >/dev/null
     nsc generate creds -a SYS -n sys > /creds/SYS.creds 2>/dev/null || (nsc add user -a SYS sys >/dev/null && nsc generate creds -a SYS -n sys > /creds/SYS.creds)
     echo operator+SYS"

# COLL1: a collector account that EXPORTS its telemetry as a public stream.
run "nsc add account COLL1 >/dev/null
     nsc edit account COLL1 --sk generate >/dev/null
     nsc add user -a COLL1 agent >/dev/null
     nsc add export -a COLL1 --subject 'netpulse.telemetry.>' --name telemetry >/dev/null
     nsc generate creds -a COLL1 -n agent > /creds/COLL1.creds
     COLL1PUB=\$(nsc describe account COLL1 -J | sed -n 's/.*\"sub\": \"\(A[A-Z0-9]*\)\".*/\1/p' | head -1)
     echo \"COLL1 export ready (\$COLL1PUB)\""

# AGG: JetStream-enabled aggregate account; IMPORTS COLL1's telemetry; bridge user.
COLL1PUB=$(run "nsc describe account COLL1 -J" | python3 -c "import sys,json;print(json.load(sys.stdin)['sub'])" 2>/dev/null || true)
run "nsc add account AGG >/dev/null
     nsc edit account AGG --js-mem-storage 256M --js-disk-storage 1G --js-streams -1 --js-consumer -1 >/dev/null
     nsc add import -a AGG --src-account ${COLL1PUB} --remote-subject 'netpulse.telemetry.>' --local-subject 'netpulse.telemetry.>' >/dev/null 2>&1 || \
       nsc add import -a AGG --account COLL1 --remote-subject 'netpulse.telemetry.>' >/dev/null 2>&1 || true
     nsc add user -a AGG bridge >/dev/null
     nsc generate creds -a AGG -n bridge > /creds/AGG.creds
     echo 'AGG (JS + import COLL1 + bridge) ready'"

run "nsc generate config --nats-resolver > /out/resolver.conf
     echo resolver.conf"
sed -i 's#dir: .*#dir: \"/data/jwt\"#; s/allow_delete: false/allow_delete: true/' out/resolver.conf

echo '--- creds ---'; ls -1 creds
echo '--- AGG account (JS + import) ---'
run "nsc describe account AGG" 2>/dev/null | grep -iE "Jetstream|Imports|telemetry|Max Mem|Max Disk|Streams" | head
echo '--- COLL1 exports ---'
run "nsc describe account COLL1" 2>/dev/null | grep -iE "Exports|telemetry" | head
