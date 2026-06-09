"""Central credential broker (credentials Option A) — deny-by-default.

A remote collector cannot read device credentials from OpenBao itself; it asks
this broker over the authenticated leaf. Built against the confused-deputy
failure from line one:

  * IDENTITY comes from the AUTHENTICATED TRANSPORT (the NATS account the request
    arrived on, which the server injects), NEVER from the message body. A
    collector-id/account/vault_path in the body is ignored.
  * The ALLOWED SET is derived SERVER-SIDE from the single authority
    (resolve.effective_collector): a collector may fetch a device's creds IFF
    effective_collector(device) == that collector. resolve.py is reused, never
    reimplemented.
  * The request can only NARROW (pick a device / protocol), never widen. The
    broker COMPUTES the vault path for an owned device and reads only that — it
    never reads a path the collector supplies, and validates the computed path
    against a strict device-credential shape.
  * The OpenBao read uses a SCOPED token (read-only on device-cred paths, NO list
    anywhere — see setup_secret_broker), so a logic bug degrades to "fails to
    fetch," never "enumerates the vault."
  * EVERY request writes one structured audit line (allow or deny) before
    returning. There is no code path that returns a secret without auditing.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("collectors.secret_broker")
audit = logging.getLogger("collectors.secret_broker.audit")

# The device-credential path shape — single source of truth mirrors
# CredentialProfile.default_vault_path ("netpulse/credentials/{pk}"). The broker
# reads ONLY paths matching this; anything else (a poisoned/edited vault_path)
# is denied rather than read.
VAULT_PATH_RE = re.compile(r"^netpulse/credentials/[0-9]+$")

ALLOW = "allow"
DENY = "deny"


def _audit_line(*, account, device_id, path, decision, reason):
    """Un-skippable: every fetch() branch calls this before it returns."""
    audit.info(
        "secret_broker decision=%s account=%s device=%s path=%s reason=%s",
        decision, account or "-", device_id if device_id is not None else "-",
        path or "-", reason,
    )
    if decision == DENY:
        # A burst of denies is the compromise signal — log loudly + best-effort
        # into the audit trail.
        logger.warning("secret_broker DENY account=%s device=%s reason=%s",
                       account or "-", device_id, reason)
    try:
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        log_event(AuditLog.EventType.CREDENTIAL_ACCESSED, username=(account or "collector"),
                  description=f"secret_broker {decision} device={device_id} ({reason})",
                  metadata={"account": account, "device_id": device_id, "decision": decision,
                            "reason": reason}, success=(decision == ALLOW))
    except Exception:  # noqa: BLE001 — the structured log line above is the un-skippable one
        pass


def resolve_collector(authenticated_account: str):
    """Map the NATS-authenticated account → an active REMOTE collector, or None.

    `authenticated_account` is the account the SERVER authenticated the leaf to;
    it is the identity, full stop.
    """
    from .models import Collector

    if not authenticated_account:
        return None
    return (
        Collector.objects.filter(
            nats_account=authenticated_account,
            collector_type=Collector.CollectorType.REMOTE,
        )
        .exclude(status=Collector.Status.REVOKED)
        .first()
    )


def _approle_configured() -> bool:
    import os
    return bool(os.environ.get("BROKER_APPROLE_ROLE_ID") and
               os.environ.get("BROKER_APPROLE_SECRET_ID"))


def _require_approle() -> bool:
    """In production the scoped AppRole is MANDATORY. Defaults to `not DEBUG`;
    a deployment can force it on with BROKER_REQUIRE_APPROLE."""
    from django.conf import settings as dj
    return bool(getattr(dj, "BROKER_REQUIRE_APPROLE", not dj.DEBUG))


def check_broker_config() -> None:
    """Fail closed at startup: a production broker with no scoped AppRole must
    REFUSE TO START rather than silently read with over-broad (platform) creds.
    Called by run_secret_broker before serving.
    """
    if _require_approle() and not _approle_configured():
        raise RuntimeError(
            "secret-broker refuses to start: BROKER_APPROLE_ROLE_ID/SECRET_ID are "
            "not set and this is a production deployment (DEBUG is false / "
            "BROKER_REQUIRE_APPROLE is set). The broker must use its least-privilege "
            "AppRole — it will NOT fall back to the platform reader. Run "
            "`manage.py setup_secret_broker` and inject the AppRole creds.")


def _scoped_read(path: str) -> dict:
    """Read a secret using the broker's SCOPED OpenBao token (least privilege).

    Authenticates via the broker AppRole (read-only on device-cred paths, no
    list). The platform-reader fallback exists ONLY for local dev and is HARD-
    GATED: when an AppRole is required (prod) but absent, this raises rather than
    escalating to broader credentials. Isolated so tests can stub it.
    """
    if _approle_configured():
        import hvac
        import os
        from django.conf import settings as dj
        client = hvac.Client(url=getattr(dj, "OPENBAO_ADDR", "http://openbao:8200"))
        client.auth.approle.login(
            role_id=os.environ["BROKER_APPROLE_ROLE_ID"],
            secret_id=os.environ["BROKER_APPROLE_SECRET_ID"])
        resp = client.secrets.kv.v2.read_secret_version(
            path=path, mount_point="secret", raise_on_deleted_version=True)
        return resp["data"]["data"]
    if _require_approle():
        # Defence-in-depth: even if check_broker_config was bypassed, never read
        # with the platform token in prod.
        raise RuntimeError("scoped AppRole required but not configured — refusing platform-reader fallback")
    # Local dev only.
    from apps.credentials import vault
    logger.warning("secret_broker: no AppRole configured — using DEV platform reader (not for prod)")
    return vault.read_secret(path)


def fetch(authenticated_account: str, request: dict) -> dict:
    """Authorize + read a device's creds for the AUTHENTICATED collector.

    `authenticated_account` MUST be the transport-authenticated account (never a
    body field). `request` may carry {"device_id", "protocol"?} and can only
    NARROW. Returns {"ok": bool, "error"?: str, "secret"?: dict}. Never returns a
    secret without an audit line.
    """
    from apps.devices.models import Device

    from .resolve import effective_collector

    req = request or {}
    raw_device = req.get("device_id")

    # 1. Identity from transport → an active remote collector, or deny.
    collector = resolve_collector(authenticated_account)
    if collector is None:
        _audit_line(account=authenticated_account, device_id=raw_device, path=None,
                    decision=DENY, reason="unknown_or_revoked_account")
        return {"ok": False, "error": "unauthorized"}

    # 2. device_id must be a clean positive int — reject bools, floats, and any
    #    non-digit string (no silent int(1.5)→1 / int("0x..") surprises).
    is_int = isinstance(raw_device, int) and not isinstance(raw_device, bool)
    is_digits = isinstance(raw_device, str) and raw_device.isdigit()
    if not (is_int or is_digits) or int(raw_device) <= 0:
        _audit_line(account=authenticated_account, device_id=raw_device, path=None,
                    decision=DENY, reason="malformed_device_id")
        return {"ok": False, "error": "bad_request"}
    device_id = int(raw_device)

    device = (Device.objects.select_related("credential_profile", "site", "collector")
              .filter(id=device_id).first())
    if device is None:
        _audit_line(account=authenticated_account, device_id=device_id, path=None,
                    decision=DENY, reason="device_not_found")
        return {"ok": False, "error": "not_found"}

    # 3. THE authority: the authenticated collector must OWN the device.
    owner = effective_collector(device)
    if owner is None or owner.id != collector.id:
        _audit_line(account=authenticated_account, device_id=device_id, path=None,
                    decision=DENY, reason="device_not_owned_by_collector")
        return {"ok": False, "error": "forbidden"}

    # 4. Broker COMPUTES the path (never a client-supplied one) + validates shape.
    profile = device.credential_profile
    path = profile.vault_path if profile else ""
    if not path or not VAULT_PATH_RE.match(path):
        _audit_line(account=authenticated_account, device_id=device_id, path=path,
                    decision=DENY, reason="no_or_bad_vault_path")
        return {"ok": False, "error": "no_credentials"}

    # 5. Scoped read; any failure denies (never widens).
    try:
        secret = _scoped_read(path)
    except Exception as exc:  # noqa: BLE001
        _audit_line(account=authenticated_account, device_id=device_id, path=path,
                    decision=DENY, reason=f"read_error:{type(exc).__name__}")
        return {"ok": False, "error": "fetch_failed"}

    # Optional narrowing to one protocol's fields (request can only narrow).
    protocol = req.get("protocol")
    if protocol in ("ssh", "snmp") and isinstance(secret, dict):
        secret = {k: v for k, v in secret.items() if k.startswith(protocol)}

    _audit_line(account=authenticated_account, device_id=device_id, path=path,
                decision=ALLOW, reason="ok")
    return {"ok": True, "secret": secret}
