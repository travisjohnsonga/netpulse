"""
Outbound-URL safety helpers (SSRF defense).

``validate_outbound_url`` enforces two things on a URL the platform is about to
fetch:

1. **Scheme allow-list** — only ``http``/``https``. Blocks ``file://``,
   ``ftp://``, ``gopher://``, ``dict://`` and friends that SSRF payloads use to
   read local files or pivot to other protocols.
2. **Cloud-metadata block** (optional, on by default) — refuses the well-known
   instance-metadata endpoints (AWS/GCP/Azure IMDS, AWS ECS task metadata,
   Alibaba, IPv6 IMDS). The host is resolved and **every** resolved IP is checked,
   so a hostname that resolves to a metadata address is caught too.

Deliberately NOT blocked: general private / RFC-1918 / loopback ranges. spane's
intended targets are private by design — the local-NLP backend
(``http://ollama:11434``, ``http://10.x``), internal service health probes, and
on-prem NetBox all live on private networks. Blocking those would break normal
operation, so this guard is intentionally narrow: bad schemes and the specific
metadata IPs only.

Callers either let ``UnsafeURLError`` propagate (admin/internal-set URLs) or
catch it and fail closed (e.g. the NLP backends return ``None``).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("http", "https")

# Cloud instance-metadata service (IMDS) endpoints. Reaching these from a
# server-side fetch is the classic SSRF→credential-theft pivot.
_METADATA_LITERALS = (
    "169.254.169.254",   # AWS / GCP / Azure / OpenStack / DigitalOcean IMDS
    "169.254.170.2",     # AWS ECS task-metadata endpoint
    "100.100.100.200",   # Alibaba Cloud metadata
    "fd00:ec2::254",     # AWS IMDS over IPv6
)


class UnsafeURLError(ValueError):
    """Raised when an outbound URL is disallowed (bad scheme or metadata target)."""


def _norm_ip(value: str) -> str | None:
    """Return the canonical form of an IP literal, or None if not an IP."""
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return None


_METADATA_IPS = frozenset(filter(None, (_norm_ip(ip) for ip in _METADATA_LITERALS)))


def is_metadata_address(value: str) -> bool:
    """True if ``value`` is (normalises to) a known cloud-metadata IP literal."""
    norm = _norm_ip(value)
    return norm is not None and norm in _METADATA_IPS


def validate_outbound_url(url: str, *, block_metadata: bool = True) -> str:
    """Validate an outbound URL; return it unchanged on success.

    Raises ``UnsafeURLError`` when the scheme isn't http/https, or (when
    ``block_metadata`` is set) when the host is, or resolves to, a cloud-metadata
    address. ``block_metadata=False`` does scheme-restriction only — use it for
    admin/internal-set URLs (e.g. service health probes) where the metadata block
    isn't wanted.
    """
    parsed = urlparse(url or "")
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"URL scheme '{scheme or '(none)'}' is not allowed; use http or https")

    if not block_metadata:
        return url

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")

    # Collect every IP the host could resolve to (a literal resolves to itself).
    candidates: set[str] = set()
    literal = _norm_ip(host)
    if literal is not None:
        candidates.add(literal)
    else:
        try:
            infos = socket.getaddrinfo(host, parsed.port or None,
                                       proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            infos = []   # unresolvable host → can't be a metadata address here
        for info in infos:
            norm = _norm_ip(info[4][0])
            if norm is not None:
                candidates.add(norm)

    if candidates & _METADATA_IPS:
        # Don't echo the resolved IP back to callers/logs verbatim beyond this.
        raise UnsafeURLError("URL resolves to a blocked cloud-metadata address")
    return url
