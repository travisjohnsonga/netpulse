"""
Helpers for returning errors to API clients without leaking internals.

CodeQL flags "information exposure through an exception": raw exception text can
carry stack frames, filesystem paths, library internals or even secrets, so it
must never be written into an HTTP response. The pattern here is always the
same — log the full detail server-side (with traceback) and hand the client a
safe, generic message.

Usage:

    from apps.core.errors import internal_error_response, safe_detail

    # Simple 500 with the default generic body:
    except Exception as exc:
        return internal_error_response(exc, logger, "fetching device telemetry")

    # Custom response shape / status — keep the contract, scrub the message:
    except SomeError as exc:
        return Response(
            {"ok": False, "message": safe_detail(exc, context="netbox test",
                                                 public="Could not connect to NetBox.")},
            status=status.HTTP_502_BAD_GATEWAY,
        )
"""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response

_log = logging.getLogger("netpulse.errors")

GENERIC_ERROR = "An internal error occurred. Please contact your administrator."


def log_internal_error(exc: BaseException, logger: logging.Logger | None = None,
                       context: str = "") -> None:
    """Log the full exception (with traceback) server-side. Returns nothing."""
    (logger or _log).error("Internal error (%s): %s", context or "unspecified",
                           exc, exc_info=True)


def safe_detail(exc: BaseException, logger: logging.Logger | None = None,
                context: str = "", public: str = GENERIC_ERROR) -> str:
    """
    Log ``exc`` server-side and return ``public`` — a safe, static message for
    the response body. Lets callers keep their own response shape/status while
    guaranteeing no exception detail reaches the client.
    """
    log_internal_error(exc, logger, context)
    return public


def internal_error_response(exc: BaseException, logger: logging.Logger | None = None,
                            context: str = "", *,
                            status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
                            public_message: str = GENERIC_ERROR,
                            extra: dict | None = None) -> Response:
    """
    Log ``exc`` internally and return a Response carrying only ``public_message``.
    ``extra`` merges extra (non-sensitive) keys into the body for endpoints whose
    clients expect a particular shape (e.g. ``{"neighbors": []}``).
    """
    log_internal_error(exc, logger, context)
    body: dict = {"error": public_message}
    if extra:
        body = {**extra, "error": public_message}
    return Response(body, status=status_code)
