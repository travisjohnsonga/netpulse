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

# Agent mTLS CA (ssl_client_certificate). The api publishes the real NetPulse
# Agent CA here (setup_agent_pki, early in its entrypoint). Wait for the REAL
# CA — not merely a non-empty file — so a placeholder left on the volume by a
# previous boot doesn't pin nginx to an unverifiable CA forever. The real CA's
# subject is "NetPulse Agent CA"; the placeholder below is "...CA Placeholder"
# (a superstring), so we detect the placeholder explicitly and treat anything
# else that parses as a real CA. Fall back to a placeholder so nginx always
# starts — agent mTLS just won't verify until the real CA lands + nginx reloads.
AGENT_CA="$SSL_DIR/agent-ca.crt"
PLACEHOLDER_CN="NetPulse Agent CA Placeholder"

# True when $AGENT_CA is a parseable x509 cert whose subject is NOT the placeholder.
agent_ca_is_real() {
    subject=$(openssl x509 -in "$AGENT_CA" -noout -subject 2>/dev/null) || return 1
    [ -n "$subject" ] || return 1
    case "$subject" in
        *"$PLACEHOLDER_CN"*) return 1 ;;
        *) return 0 ;;
    esac
}

i=0
while ! agent_ca_is_real && [ "$i" -lt 30 ]; do
    i=$((i + 1)); sleep 1
done

if agent_ca_is_real; then
    echo "[entrypoint] real agent CA published — agent mTLS active"
elif [ ! -s "$AGENT_CA" ]; then
    echo "[entrypoint] agent CA not published yet — seeding placeholder (agent mTLS inactive until reload)"
    openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:secp384r1 -nodes \
        -keyout /tmp/agent-ca-placeholder.key -out "$AGENT_CA" -days 3650 \
        -subj "/CN=$PLACEHOLDER_CN" >/dev/null 2>&1
else
    echo "[entrypoint] agent CA still a placeholder — agent mTLS inactive until the real CA lands and nginx reloads"
fi

# Runs as an nginx /docker-entrypoint.d/ hook — return so the launcher
# continues to the next script and finally starts nginx.
