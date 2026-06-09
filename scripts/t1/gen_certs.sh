#!/usr/bin/env bash
# T1a transport (mTLS) material: a CA + hub server cert + a good edge client cert,
# PLUS a second CA and an edge cert signed by it (the negative: a cert the hub's
# CA does NOT trust). In T1b these are replaced by OpenBao-PKI-issued certs.
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p certs; cd certs

ca() { openssl req -x509 -newkey rsa:2048 -nodes -keyout "$1-key.pem" -out "$1.pem" -days 3650 -subj "/CN=$2" -sha256 2>/dev/null; }
leaf() { # $1=name $2=CAprefix $3=SAN $4=EKU
  openssl req -newkey rsa:2048 -nodes -keyout "$1-key.pem" -out "$1.csr" -subj "/CN=$1" 2>/dev/null
  openssl x509 -req -in "$1.csr" -CA "$2.pem" -CAkey "$2-key.pem" -CAcreateserial -out "$1-cert.pem" -days 3650 -sha256 \
    -extfile <(printf "subjectAltName=%s\nextendedKeyUsage=%s\n" "$3" "$4") 2>/dev/null
}

ca ca   "NetPulse-T1-CA"
ca badca "NetPulse-T1-UNTRUSTED-CA"
leaf hub   ca    "DNS:hub,DNS:leafproxy,DNS:localhost" "serverAuth,clientAuth"
leaf edge  ca    "DNS:edge"      "clientAuth,serverAuth"
leaf edgebad badca "DNS:edgebad" "clientAuth,serverAuth"   # signed by the UNTRUSTED CA
rm -f ./*.csr ./*.srl
echo "T1 certs:"; ls -1 *-cert.pem ca.pem badca.pem
