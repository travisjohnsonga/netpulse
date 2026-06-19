"""JWT authentication for WebSocket connections.

Browsers can't set an ``Authorization`` header on a WebSocket handshake, so the
SPA passes the access token as the second WebSocket subprotocol:

    new WebSocket(url, ["bearer", "<jwt>"])

This middleware validates that token with SimpleJWT and populates
``scope["user"]``; the consumers reject anonymous connections (close 4401). A
``?token=`` query-string fallback is accepted for non-browser clients.

Without this, the realtime endpoints (alerts / telemetry / device status) would
stream live inventory, alert, and topology data to any unauthenticated client
that can reach the host — bypassing the entire DRF permission layer.
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser

logger = logging.getLogger(__name__)


@database_sync_to_async
def _user_from_token(raw_token: str):
    """Resolve a JWT to a user, or AnonymousUser if invalid/expired."""
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    auth = JWTAuthentication()
    try:
        validated = auth.get_validated_token(raw_token)
        return auth.get_user(validated)
    except (InvalidToken, TokenError) as exc:
        logger.debug("WS JWT rejected: %s", exc)
        return AnonymousUser()
    except Exception as exc:  # noqa: BLE001 — never let auth errors 500 the handshake
        logger.warning("WS JWT validation error: %s", exc)
        return AnonymousUser()


def ws_subprotocol(scope) -> str | None:
    """The subprotocol to echo in accept(): "bearer" when the client offered it.

    Per RFC 6455 the server should confirm one of the client's offered
    subprotocols; echoing "bearer" keeps the browser handshake clean.
    """
    return "bearer" if "bearer" in (scope.get("subprotocols") or []) else None


def _extract_token(scope) -> str | None:
    # Preferred: token as the 2nd WebSocket subprotocol ("bearer", "<jwt>").
    subprotocols = scope.get("subprotocols") or []
    if len(subprotocols) >= 2 and subprotocols[0] == "bearer":
        return subprotocols[1]
    # Fallback: ?token= query string (non-browser clients).
    qs = parse_qs((scope.get("query_string") or b"").decode())
    token = qs.get("token")
    return token[0] if token else None


class JWTAuthMiddleware:
    """Channels middleware: set scope['user'] from a JWT when one is supplied.

    Leaves an existing (session) user in place when no token is present, so it
    composes under AuthMiddlewareStack — JWT takes precedence when offered.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket":
            token = _extract_token(scope)
            if token:
                scope["user"] = await _user_from_token(token)
        return await self.inner(scope, receive, send)
