"""
Service-check handlers + pure status/state logic.

Handlers are async and agentless — they open an outbound connection to the
target and return a plain dict::

    {"status": "up|down|degraded", "response_time_ms": float|None,
     "error": str, "details": {...}}

The pure functions (``classify_status`` / ``next_state``) hold all the
threshold and flap-suppression logic so they can be unit-tested without a
network. Stage 1 ships HTTP/HTTPS and TCP; later stages register more handlers
in ``HANDLERS`` without touching the engine.
"""
from __future__ import annotations

import asyncio
import time

UP = "up"
DOWN = "down"
DEGRADED = "degraded"
UNKNOWN = "unknown"


def classify_status(ok: bool, response_time_ms: float | None,
                    warn_ms: int | None, crit_ms: int | None) -> str:
    """
    Map a successful/failed probe + its latency to up/degraded/down.

    A failed probe is always ``down``. A successful probe is ``down`` when it
    breaches the critical latency, ``degraded`` when it breaches the warning
    latency, else ``up``. Thresholds are optional.
    """
    if not ok:
        return DOWN
    if crit_ms is not None and response_time_ms is not None and response_time_ms > crit_ms:
        return DOWN
    if warn_ms is not None and response_time_ms is not None and response_time_ms > warn_ms:
        return DEGRADED
    return UP


def next_state(prev_status: str, consecutive_failures: int, result_status: str,
               failures_before_alert: int) -> tuple[str, int, str | None]:
    """
    Decide the check's effective status, failure counter and any alert to raise.

    Returns ``(effective_status, new_consecutive_failures, alert_kind)`` where
    alert_kind is one of ``down``/``recovery``/``degraded``/``None``.

    Flap suppression: a single failed probe does not flip the check to ``down``
    or alert — it takes ``failures_before_alert`` consecutive failures. Recovery
    and degraded transitions alert on the first observation.
    """
    if result_status == DOWN:
        failures = consecutive_failures + 1
        if failures >= max(1, failures_before_alert):
            alert = "down" if prev_status != DOWN else None
            return DOWN, failures, alert
        # Not yet confirmed down — hold the previous (or unknown) status.
        return (prev_status if prev_status != UNKNOWN else UNKNOWN), failures, None

    # Successful probe (up or degraded) resets the failure streak.
    if prev_status == DOWN:
        return result_status, 0, "recovery"
    if result_status == DEGRADED and prev_status != DEGRADED:
        return DEGRADED, 0, "degraded"
    return result_status, 0, None


# ── Handlers ──────────────────────────────────────────────────────────────────

async def check_tcp(check: dict) -> dict:
    """Open a TCP connection (optionally send/expect a line) and time it."""
    host = check["host"]
    port = check["effective_port"]
    if not port:
        return {"status": DOWN, "response_time_ms": None,
                "error": "no port for TCP check", "details": {}}
    cfg = check.get("config") or {}
    timeout = check.get("timeout_seconds", 10)
    start = time.monotonic()
    reader = writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout)
        connect_ms = (time.monotonic() - start) * 1000
        details: dict = {"connect_time_ms": round(connect_ms, 2)}

        send = cfg.get("send")
        if send:
            writer.write(send.encode())
            await writer.drain()
        expect = cfg.get("expect")
        if expect:
            data = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            text = data.decode(errors="replace")
            details["matched"] = expect in text
            if not details["matched"]:
                return {"status": DOWN, "response_time_ms": round(connect_ms, 2),
                        "error": f"expected {expect!r} not in response", "details": details}
        rt = (time.monotonic() - start) * 1000
        return {"status": UP, "response_time_ms": round(rt, 2), "error": "", "details": details}
    except asyncio.TimeoutError:
        return {"status": DOWN, "response_time_ms": None,
                "error": f"timeout after {timeout}s", "details": {}}
    except (OSError, ConnectionError) as exc:
        return {"status": DOWN, "response_time_ms": None, "error": str(exc), "details": {}}
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass


async def check_http(check: dict) -> dict:
    """
    HTTP/HTTPS probe via aiohttp: time the request and validate the status code
    (and optionally a body substring). Scheme follows the check_type.
    """
    import aiohttp

    cfg = check.get("config") or {}
    scheme = "https" if check["check_type"] == "https" else "http"
    host = check["host"]
    port = check["effective_port"]
    path = cfg.get("path", "/")
    if not path.startswith("/"):
        path = "/" + path
    # Only append a port when it isn't the scheme default.
    default = 443 if scheme == "https" else 80
    netloc = host if (not port or port == default) else f"{host}:{port}"
    url = f"{scheme}://{netloc}{path}"

    method = (cfg.get("method") or "GET").upper()
    expected = cfg.get("expected_status") or [200]
    expected_body = cfg.get("expected_body")
    headers = cfg.get("headers") or {}
    verify_ssl = cfg.get("verify_ssl", True)
    follow = cfg.get("follow_redirects", True)
    timeout = check.get("timeout_seconds", 10)

    start = time.monotonic()
    try:
        ct = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=ct) as session:
            async with session.request(
                method, url, headers=headers, allow_redirects=follow,
                ssl=None if verify_ssl else False,
            ) as resp:
                body = await resp.text()
                rt = (time.monotonic() - start) * 1000
                details = {
                    "status_code": resp.status,
                    "redirect_count": len(resp.history),
                }
                if resp.status not in expected:
                    return {"status": DOWN, "response_time_ms": round(rt, 2),
                            "error": f"status {resp.status} not in {expected}",
                            "details": details}
                if expected_body is not None:
                    matched = expected_body in body
                    details["body_match"] = matched
                    if not matched:
                        return {"status": DOWN, "response_time_ms": round(rt, 2),
                                "error": "expected body not found", "details": details}
                return {"status": UP, "response_time_ms": round(rt, 2),
                        "error": "", "details": details}
    except asyncio.TimeoutError:
        return {"status": DOWN, "response_time_ms": None,
                "error": f"timeout after {timeout}s", "details": {}}
    except Exception as exc:  # aiohttp.ClientError + DNS/SSL errors
        return {"status": DOWN, "response_time_ms": None, "error": str(exc), "details": {}}


# check_type → async handler. Later stages add icmp/dns/tls/smtp/ssh/…
HANDLERS = {
    "http": check_http,
    "https": check_http,
    "tcp": check_tcp,
}


async def run_check(check: dict) -> dict:
    """
    Dispatch a check dict to its handler, classify the result against the
    configured latency thresholds, and never raise — failures become a DOWN
    result so the engine can record and alert on them.
    """
    handler = HANDLERS.get(check["check_type"])
    if handler is None:
        return {"status": DOWN, "response_time_ms": None,
                "error": f"unsupported check_type {check['check_type']}", "details": {}}
    try:
        result = await handler(check)
    except asyncio.TimeoutError:
        return {"status": DOWN, "response_time_ms": None, "error": "timeout", "details": {}}
    except Exception as exc:
        return {"status": DOWN, "response_time_ms": None, "error": str(exc), "details": {}}

    # Re-classify a successful probe against latency thresholds (down beats
    # degraded beats up); a handler-reported DOWN stays down.
    result["status"] = classify_status(
        ok=result["status"] != DOWN,
        response_time_ms=result.get("response_time_ms"),
        warn_ms=check.get("response_time_warning_ms"),
        crit_ms=check.get("response_time_critical_ms"),
    )
    return result
