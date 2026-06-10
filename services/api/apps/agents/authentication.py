"""mTLS authentication for agent metric/role-check ingestion.

nginx terminates the agent's mTLS connection, verifies the client cert against
the NetPulse Agent CA, and forwards the result to Django as request headers:

    X-Agent-Verified     -> $ssl_client_verify  ("SUCCESS" when CA-verified)
    X-Agent-Cert-Serial  -> $ssl_client_serial  (uppercase hex, no separators)

We only trust these when verification SUCCEEDED, and resolve the agent by cert
serial. nginx ($ssl_client_serial: "1AB2…") and OpenBao (Agent.cert_serial:
"1a:b2:…") format serials differently, so both sides are normalized before
comparison (strip separators, uppercase).

Trust boundary: these headers are authoritative only because nginx sets them
from the verified TLS session (and overwrites any client-supplied value on the
ingest locations). The api upstream must not be reachable bypassing nginx in
production — see the agent ingestion notes in CLAUDE.md.
"""
from __future__ import annotations

from rest_framework.authentication import BaseAuthentication

from .models import Agent

VERIFIED_HEADER = "HTTP_X_AGENT_VERIFIED"
SERIAL_HEADER = "HTTP_X_AGENT_CERT_SERIAL"


def normalize_serial(serial: str) -> str:
    """Canonicalize a cert serial: drop ':'/whitespace separators, uppercase.
    Makes nginx's $ssl_client_serial and OpenBao's serial_number comparable.
    """
    return (serial or "").replace(":", "").replace(" ", "").strip().upper()


class AgentCertAuthentication(BaseAuthentication):
    """Authenticate an agent from the nginx-verified mTLS client-cert serial."""

    def authenticate(self, request):
        if request.META.get(VERIFIED_HEADER, "") != "SUCCESS":
            return None
        want = normalize_serial(request.META.get(SERIAL_HEADER, ""))
        if not want:
            return None
        # Match a non-revoked agent by normalized serial. Server fleets are
        # small (tens–hundreds), so the normalized scan is cheap; if it ever
        # needs to scale, store the normalized serial and query it directly.
        for agent in Agent.objects.exclude(status=Agent.Status.REVOKED):
            if agent.cert_serial and normalize_serial(agent.cert_serial) == want:
                return (agent, None)
        return None

    # No authenticate_header → DRF renders a failed auth as 401 (not a 403
    # challenge with WWW-Authenticate, which makes no sense for mTLS).
