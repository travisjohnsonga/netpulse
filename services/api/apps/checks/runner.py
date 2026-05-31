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


def icmp_status(packet_loss_pct: float, is_alive: bool) -> str:
    """ICMP: up <10% loss, degraded 10-50%, down >50% or unreachable."""
    if not is_alive or packet_loss_pct > 50:
        return DOWN
    if packet_loss_pct >= 10:
        return DEGRADED
    return UP


def tls_status(days_remaining: int, warn_days: int, crit_days: int, valid: bool = True) -> str:
    """TLS: down if expired/invalid, degraded within warn window, else up."""
    if not valid or days_remaining <= 0:
        return DOWN
    if days_remaining <= warn_days or days_remaining <= crit_days:
        return DEGRADED
    return UP


def dns_status(resolved: bool, answer_matches) -> str:
    """DNS: down if unresolved, degraded if resolved but answer mismatches, else up."""
    if not resolved:
        return DOWN
    if answer_matches is False:
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


async def check_icmp(check: dict) -> dict:
    """
    ICMP echo via icmplib. Uses unprivileged datagram ICMP (no raw socket) so it
    works as the non-root container user — the check-engine service sets
    net.ipv4.ping_group_range to permit this. Falls back to privileged raw
    sockets if datagram ICMP is disallowed (e.g. running as root with NET_RAW).
    """
    from icmplib import async_ping

    cfg = check.get("config") or {}
    count = int(cfg.get("count", 4))
    size = int(cfg.get("packet_size", 56))
    timeout = check.get("timeout_seconds", 10)
    try:
        try:
            host = await async_ping(check["host"], count=count, interval=0.2,
                                    timeout=timeout, payload_size=size, privileged=False)
        except Exception:
            host = await async_ping(check["host"], count=count, interval=0.2,
                                    timeout=timeout, payload_size=size, privileged=True)
    except Exception as exc:
        return {"status": DOWN, "response_time_ms": None, "error": str(exc), "details": {}}

    loss = round(host.packet_loss * 100, 1)  # icmplib reports a 0..1 fraction
    details = {
        "packet_loss_pct": loss,
        "avg_rtt_ms": round(host.avg_rtt, 2),
        "min_rtt_ms": round(host.min_rtt, 2),
        "max_rtt_ms": round(host.max_rtt, 2),
        "jitter_ms": round(host.jitter, 2),
    }
    status = icmp_status(loss, host.is_alive)
    error = "" if status == UP else (f"packet loss {loss:.0f}%" if host.is_alive else "host unreachable")
    rt = round(host.avg_rtt, 2) if host.is_alive else None
    return {"status": status, "response_time_ms": rt, "error": error, "details": details}


def _dns_answers(records, rtype: str) -> list:
    out = []
    for r in (records or []):
        for attr in ("host", "cname", "text", "nsname", "name"):
            v = getattr(r, attr, None)
            if v:
                out.append(v if isinstance(v, str) else str(v))
                break
        else:
            out.append(str(r))
    return out


async def check_dns(check: dict) -> dict:
    """Resolve a record via aiodns; optionally assert the answer."""
    import aiodns

    cfg = check.get("config") or {}
    query = cfg.get("query") or check["host"]
    rtype = (cfg.get("record_type") or "A").upper()
    expected = cfg.get("expected_answer")
    nameserver = cfg.get("nameserver")
    timeout = check.get("timeout_seconds", 10)

    loop = asyncio.get_event_loop()
    resolver = aiodns.DNSResolver(loop=loop, timeout=timeout)
    if nameserver:
        resolver.nameservers = [nameserver]

    start = time.monotonic()
    try:
        records = await resolver.query(query, rtype)
    except Exception as exc:
        return {"status": DOWN, "response_time_ms": None,
                "error": f"DNS {rtype} {query}: {exc}", "details": {}}
    rt = round((time.monotonic() - start) * 1000, 2)

    answers = _dns_answers(records, rtype)
    details = {"answers": answers, "resolve_time_ms": rt, "record_type": rtype}
    answer_matches = None
    if expected:
        answer_matches = expected in answers
        details["answer_matches"] = answer_matches
    status = dns_status(resolved=True, answer_matches=answer_matches)
    error = "" if status == UP else f"answer {answers} != expected {expected}"
    return {"status": status, "response_time_ms": rt, "error": error, "details": details}


def _cert_field(rdn_seq, key: str):
    """Pull a value (e.g. commonName / organizationName) from a getpeercert RDN sequence."""
    for rdn in (rdn_seq or ()):
        for k, v in rdn:
            if k == key:
                return v
    return None


async def check_tls(check: dict) -> dict:
    """
    Open a TLS connection (stdlib ssl) and inspect the server certificate.

    Verifies the chain against system trust (hostname check disabled so a valid
    cert presented to the wrong name still yields days_remaining); an untrusted
    or self-signed cert fails verification and is reported down.
    """
    import ssl
    from datetime import datetime, timezone

    cfg = check.get("config") or {}
    warn_days = int(cfg.get("warn_days", 30))
    crit_days = int(cfg.get("critical_days", 7))
    host = check["host"]
    port = check["effective_port"] or 443
    timeout = check.get("timeout_seconds", 10)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # we want cert details even on a name mismatch

    start = time.monotonic()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout=timeout)
        rt = round((time.monotonic() - start) * 1000, 2)
        cert = writer.get_extra_info("ssl_object").getpeercert()
    except ssl.SSLCertVerificationError as exc:
        return {"status": DOWN, "response_time_ms": None,
                "error": f"certificate invalid: {exc.verify_message or exc}",
                "details": {"valid": False, "chain_valid": False}}
    except asyncio.TimeoutError:
        return {"status": DOWN, "response_time_ms": None, "error": f"timeout after {timeout}s", "details": {}}
    except Exception as exc:
        return {"status": DOWN, "response_time_ms": None, "error": str(exc), "details": {}}
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError, ssl.SSLError):
                pass

    not_after = cert.get("notAfter")
    try:
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        days = (expires - datetime.now(timezone.utc)).days
    except (TypeError, ValueError):
        return {"status": DOWN, "response_time_ms": rt,
                "error": "could not parse certificate expiry", "details": {"valid": False}}

    details = {
        "days_remaining": days,
        "cert_cn": _cert_field(cert.get("subject"), "commonName"),
        "issuer": _cert_field(cert.get("issuer"), "organizationName") or _cert_field(cert.get("issuer"), "commonName"),
        "valid": days > 0,
        "chain_valid": True,
        "not_after": not_after,
        "connect_time_ms": rt,
    }
    status = tls_status(days, warn_days, crit_days, valid=True)
    error = "" if status == UP else ("certificate expired" if days <= 0 else f"{days} days remaining")
    return {"status": status, "response_time_ms": rt, "error": error, "details": details}


async def check_smtp(check: dict) -> dict:
    """Connect + EHLO (no auth, no send) via aiosmtplib."""
    import aiosmtplib

    cfg = check.get("config") or {}
    helo = cfg.get("helo", "netpulse.local")
    use_starttls = bool(cfg.get("starttls", False))
    host = check["host"]
    port = check["effective_port"] or 25
    timeout = check.get("timeout_seconds", 10)

    start = time.monotonic()
    client = aiosmtplib.SMTP(hostname=host, port=port, timeout=timeout, start_tls=False)
    try:
        banner_resp = await client.connect()
        await client.ehlo(helo)
        starttls_supported = client.supports_extension("starttls")
        if use_starttls and starttls_supported:
            await client.starttls()
        try:
            await client.quit()
        except Exception:
            pass
    except Exception as exc:
        return {"status": DOWN, "response_time_ms": None, "error": str(exc), "details": {}}

    rt = round((time.monotonic() - start) * 1000, 2)
    details = {
        "connect_time_ms": rt,
        "banner": getattr(banner_resp, "message", "") or "",
        "starttls_supported": bool(starttls_supported),
    }
    return {"status": UP, "response_time_ms": rt, "error": "", "details": details}


async def check_ssh_banner(check: dict) -> dict:
    """
    TCP-connect and read the SSH identification banner. Never logs in — purely
    agentless and non-invasive. Optionally asserts an expected banner substring.
    """
    cfg = check.get("config") or {}
    expected = cfg.get("expected_banner")
    host = check["host"]
    port = check["effective_port"] or 22
    timeout = check.get("timeout_seconds", 10)

    start = time.monotonic()
    writer = None
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        banner = line.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        return {"status": DOWN, "response_time_ms": None, "error": f"timeout after {timeout}s", "details": {}}
    except (OSError, ConnectionError) as exc:
        return {"status": DOWN, "response_time_ms": None, "error": str(exc), "details": {}}
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

    rt = round((time.monotonic() - start) * 1000, 2)
    details = {"connect_time_ms": rt, "banner": banner}
    if not banner:
        return {"status": DOWN, "response_time_ms": rt, "error": "no banner received", "details": details}
    if expected and expected not in banner:
        return {"status": DEGRADED, "response_time_ms": rt,
                "error": f"banner {banner!r} does not contain {expected!r}", "details": details}
    return {"status": UP, "response_time_ms": rt, "error": "", "details": details}


# check_type → async handler.
HANDLERS = {
    "http": check_http,
    "https": check_http,
    "tcp": check_tcp,
    "icmp": check_icmp,
    "dns": check_dns,
    "tls": check_tls,
    "smtp": check_smtp,
    "ssh": check_ssh_banner,
    "ssh_banner": check_ssh_banner,
}

# Check types whose status is domain-specific (packet loss, cert expiry, answer
# match) and must NOT be re-graded by the latency thresholds in run_check.
_DOMAIN_STATUS_TYPES = {"icmp", "dns", "tls"}


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

    # Latency reclassification applies only to an "up" result from a latency-
    # sensitive check: it may downgrade up → degraded/down, but never overrides a
    # handler's degraded/down, and is skipped for domain-status types (icmp/dns/
    # tls own their status via packet loss / answer match / cert expiry).
    if result["status"] == UP and check["check_type"] not in _DOMAIN_STATUS_TYPES:
        result["status"] = classify_status(
            ok=True,
            response_time_ms=result.get("response_time_ms"),
            warn_ms=check.get("response_time_warning_ms"),
            crit_ms=check.get("response_time_critical_ms"),
        )
    return result
