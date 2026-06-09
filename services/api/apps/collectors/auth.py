"""Secrets handling for remote-collector identity.

Per-collector API keys and one-time enrollment tokens are stored only as bcrypt
hashes (never plaintext, per the Security Rules). Plaintext is generated here,
returned to the caller exactly once, and immediately discarded.
"""
from __future__ import annotations

import secrets

import bcrypt

# Plaintext lengths (token_urlsafe yields ~1.3 chars/byte).
_API_KEY_BYTES = 32          # ~43-char urlsafe key
_ENROLL_TOKEN_BYTES = 24     # ~32-char urlsafe token


def generate_api_key() -> str:
    """A fresh opaque collector API key (plaintext — store only its hash)."""
    return "npc_" + secrets.token_urlsafe(_API_KEY_BYTES)


def generate_enrollment_token() -> str:
    """A fresh one-time enrollment/bootstrap token (plaintext — store its hash)."""
    return "npe_" + secrets.token_urlsafe(_ENROLL_TOKEN_BYTES)


def hash_secret(plaintext: str) -> str:
    """bcrypt-hash a secret; returns a utf-8 string safe for a CharField."""
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_secret(plaintext: str, hashed: str) -> bool:
    """Constant-time bcrypt verify. False on any malformed input (never raises)."""
    if not plaintext or not hashed:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False
