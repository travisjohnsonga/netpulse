"""
Shared redaction helpers for ingest plugins.

Credentials (API keys, tokens, webhook secrets) must never reach logs or be
persisted on alert/device records in clear text. Use these helpers whenever a
dict that may carry secrets is logged or stored — keep the keys for context,
mask the values.
"""
from __future__ import annotations

# Header/field names whose values are credentials and must be masked.
SENSITIVE_KEYS = frozenset({
    "authorization", "x-cisco-meraki-api-key", "api-key", "api_key", "apikey",
    "sharedsecret", "shared_secret", "token", "password", "secret", "key",
})

_REDACTED = "***REDACTED***"


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    # Exact match, or the name contains a credential-ish substring.
    return k in SENSITIVE_KEYS or any(s in k for s in ("key", "token", "secret", "auth", "password"))


def scrub_headers(headers: dict) -> dict:
    """Return a copy of ``headers`` with sensitive values masked, safe for logging."""
    return {k: (_REDACTED if _is_sensitive(str(k)) else v) for k, v in headers.items()}


def redact_dict(data: dict) -> dict:
    """Return a shallow copy of ``data`` with sensitive values masked (e.g. before
    persisting a raw webhook payload that may contain a shared secret)."""
    return {k: (_REDACTED if _is_sensitive(str(k)) else v) for k, v in data.items()}
