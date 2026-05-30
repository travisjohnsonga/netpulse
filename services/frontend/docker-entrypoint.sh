#!/bin/sh
# Ensure an HTTPS cert/key exist so nginx can start with the TLS server block.
# The NetPulse API manages the real certificate on the shared ssl-certs volume
# (generate self-signed, CSR, or upload). This only bootstraps a temporary
# self-signed pair on first boot when the volume is empty — it is replaced as
# soon as an admin installs a certificate via Settings → Certificates.
set -e

SSL_DIR=/etc/nginx/ssl
mkdir -p "$SSL_DIR"

if [ ! -f "$SSL_DIR/netpulse.crt" ] || [ ! -f "$SSL_DIR/netpulse.key" ]; then
    echo "[entrypoint] no HTTPS cert found — generating a temporary self-signed pair"
    openssl req -x509 -newkey rsa:2048 -nodes \
        -keyout "$SSL_DIR/netpulse.key" \
        -out "$SSL_DIR/netpulse.crt" \
        -days 825 \
        -subj "/CN=netpulse.local" \
        -addext "subjectAltName=DNS:netpulse.local" >/dev/null 2>&1
    chmod 600 "$SSL_DIR/netpulse.key"
fi

# Ensure a CA trust bundle exists so nginx's ssl_trusted_certificate directive
# always resolves. The API rebuilds this (system roots + admin-added CAs) when
# trusted CA certs are managed via Settings → System → Trusted CA Certificates;
# here we just seed it from the image's system roots on first boot.
if [ ! -f "$SSL_DIR/ca-bundle.crt" ]; then
    if [ -f /etc/ssl/certs/ca-certificates.crt ]; then
        cp /etc/ssl/certs/ca-certificates.crt "$SSL_DIR/ca-bundle.crt"
    else
        : > "$SSL_DIR/ca-bundle.crt"
    fi
fi

# Runs as an nginx /docker-entrypoint.d/ hook — return so the launcher
# continues to the next script and finally starts nginx.
