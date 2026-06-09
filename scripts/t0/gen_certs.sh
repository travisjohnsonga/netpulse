#!/usr/bin/env bash
# T0 mTLS material: a throwaway CA + a hub server cert + an edge client cert.
# The hub cert's SAN includes `leafproxy` because the edge dials the cuttable
# socat proxy (TLS is end-to-end edge↔hub through the passthrough). Throwaway —
# do NOT reuse; the real per-collector certs come from the OpenBao PKI engine.
set -euo pipefail
cd "$(dirname "$0")/certs"

gen() { openssl req -newkey rsa:2048 -nodes -keyout "$1-key.pem" -out "$1.csr" -subj "/CN=$1" 2>/dev/null; }
sign() { # $1=name  $2=SAN  $3=EKU
  openssl x509 -req -in "$1.csr" -CA ca.pem -CAkey ca-key.pem -CAcreateserial \
    -out "$1-cert.pem" -days 3650 -sha256 \
    -extfile <(printf "subjectAltName=%s\nextendedKeyUsage=%s\n" "$2" "$3") 2>/dev/null
}

# CA
openssl req -x509 -newkey rsa:2048 -nodes -keyout ca-key.pem -out ca.pem -days 3650 \
  -subj "/CN=NetPulse-T0-CA" -sha256 2>/dev/null

# Hub server cert (also valid as a client for mutual checks)
gen hub
sign hub "DNS:hub,DNS:leafproxy,DNS:localhost" "serverAuth,clientAuth"

# Edge client cert (presented on the outbound leaf connection)
gen edge
sign edge "DNS:edge" "clientAuth,serverAuth"

rm -f ./*.csr ca.srl
echo "T0 certs generated:"
ls -1 *.pem
