"""
Single, spoof-resistant client-IP derivation.

Two subsystems need to know "who is the client": DRF throttling (rate-limiting /
login lockout) and forensic IP capture (audit log, failed-login records). If they
disagree on *which* ``X-Forwarded-For`` hop is the real client, an attacker can
forge a leading XFF entry that poisons the audit trail / misattributes a security
event while the throttle keys on a different IP. So both must go through the *same*
derivation — this module — and never re-implement it.

Algorithm (mirrors DRF's ``BaseThrottle.get_ident``): with ``NUM_PROXIES`` set to
the real number of trusted reverse proxies in front of the API, the client IP is
the ``NUM_PROXIES``-th entry counted **from the right** of ``X-Forwarded-For``.
The right-most entries are appended by your own trusted proxies (nginx/LB) and a
client cannot forge them; everything to their left is client-supplied and not
trusted.

One deliberate departure from stock DRF — fail closed:

    If ``X-Forwarded-For`` is absent, empty/malformed, or has FEWER than
    ``NUM_PROXIES`` hops, we return ``REMOTE_ADDR`` (the unspoofable direct
    socket peer) instead of clamping to the left-most (client-supplied) entry.

DRF clamps to ``addrs[0]`` in the short-header case, which is exactly the
attacker-controlled value we must never trust. ``REMOTE_ADDR`` is the proxy in a
correct deployment, so keying on it is safe (worst case, all such requests share
a bucket — it can never be spoofed past a limit). ``NUM_PROXIES`` must match the
real proxy count (see settings / ``.env`` ``NUM_PROXIES``); setting it too high
re-opens the spoofing hole.
"""
from __future__ import annotations

from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


def get_client_ip(request) -> str | None:
    """The real client IP, honouring ``NUM_PROXIES`` from the right (fail-closed)."""
    if request is None:
        return None
    meta = request.META
    remote_addr = meta.get("REMOTE_ADDR") or None
    num_proxies = api_settings.NUM_PROXIES

    # NUM_PROXIES is None => DRF's "trust the entire XFF" mode. Our settings always
    # pin it to an int, but handle it like DRF for parity if ever unset.
    if num_proxies is None:
        xff = meta.get("HTTP_X_FORWARDED_FOR")
        return ("".join(xff.split()) if xff else remote_addr) or None

    # No trusted proxies (direct exposure): the socket peer is the only truth.
    if num_proxies == 0:
        return remote_addr

    xff = meta.get("HTTP_X_FORWARDED_FOR", "") or ""
    addrs = [a.strip() for a in xff.split(",") if a.strip()]
    # Header missing/malformed or shorter than the trusted chain → the expected
    # proxies did not all append, so the header is untrustworthy. Fail closed.
    if len(addrs) < num_proxies:
        return remote_addr
    # The NUM_PROXIES-th hop from the right is what our outermost trusted proxy
    # saw; entries to its left are client-supplied and ignored.
    return addrs[-num_proxies] or remote_addr


class _TrustedProxyIdentMixin:
    """Routes a DRF throttle's client identification through :func:`get_client_ip`,
    so rate-limiting and forensic IP capture can never drift apart."""

    def get_ident(self, request):  # noqa: D102 — overrides BaseThrottle.get_ident
        return get_client_ip(request)


class TrustedProxyScopedRateThrottle(_TrustedProxyIdentMixin, ScopedRateThrottle):
    """``ScopedRateThrottle`` keyed on the spoof-resistant client IP."""


class TrustedProxySimpleRateThrottle(_TrustedProxyIdentMixin, SimpleRateThrottle):
    """``SimpleRateThrottle`` base keyed on the spoof-resistant client IP."""
