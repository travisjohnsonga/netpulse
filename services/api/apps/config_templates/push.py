"""Render a template for a device and push it over SSH (Netmiko).

Reuses the same connection path as telemetry config push: resolve the device's
credential profile, read the SSH password from OpenBao, pick a Netmiko
device_type from vendor/platform, and ``send_config_set`` the rendered lines.
Every attempt is audit-logged; raw exceptions are logged server-side and never
returned to the client.
"""

from __future__ import annotations

import logging
import re

from apps.credentials import vault

from .render import render_template, render_to_lines

logger = logging.getLogger(__name__)

# Junos rejects a bad config line by PRINTING an error and continuing —
# Netmiko surfaces no exception. Match the CLI's error markers at line start
# (the "^" anchors keep ordinary config text from false-matching).
_JUNOS_LOAD_ERR_RE = re.compile(
    r"(?m)^\s*(syntax error|error:|unknown command|missing argument)", re.IGNORECASE)


def audit_push(template, device, request, success: bool, error: str = "") -> None:
    """Record a config-push attempt in the unified audit trail."""
    from apps.core.audit import log_event
    from apps.core.models import AuditLog

    logger.info("config-template push: template=%s device=%s user=%s success=%s",
                template.name, device.hostname,
                getattr(request.user, "username", "?"), success)
    log_event(
        AuditLog.EventType.CONFIG_PUSHED, request=request, target=device,
        description=f"Template '{template.name}' pushed to {device.hostname}",
        metadata={"template": template.name, "category": template.category},
        success=success, error_message=(error or "")[:512],
    )


def _result(device, success: bool, error: str = "") -> dict:
    return {"device_id": device.id, "hostname": device.hostname,
            "success": success, "error": error}


def push_junos_private(conn, lines) -> str:
    """Load ``lines`` into an isolated Junos PRIVATE candidate and commit.

    Junos is the odd one out, in two ways the generic Netmiko flow misses:

    1. **Commit is mandatory.** ``send_config_set`` only LOADS ``set`` lines
       into the CANDIDATE config — nothing takes effect until an explicit
       commit. (Cisco IOS / FortiOS apply each line immediately, which is why
       pushes "worked" everywhere but silently no-opped on Junos: Netmiko
       reported success, the audit log recorded success, and the device's
       active config was untouched, the half-loaded candidate lingering
       across sessions.)
    2. **Shared config mode is unsafe for automation.** The default
       ``configure`` shares ONE candidate with any concurrent human session —
       spane's commit would sweep their half-finished edits along.
       ``configure private`` gives spane an isolated candidate that commits
       independently, without locking humans out (unlike ``configure
       exclusive``); Junos even REFUSES private entry while the shared
       candidate is dirty, so a lingering human edit fails the push instead
       of being silently committed.

    A commit can fail even when every line loaded fine (semantic validation —
    e.g. a referenced object doesn't exist). On any failure the private
    candidate is discarded (``rollback 0``; private candidates also evaporate
    when the session exits config mode) and the error re-raised so the caller
    reports a REAL failure. Config mode is exited on every path.

    Returns the combined device output (load + commit) for display.
    """
    out = ""
    try:
        out = conn.send_config_set(
            lines, read_timeout=30,
            config_mode_command="configure private",
            exit_config_mode=False,          # stay in config mode for the commit
        )
        # Junos prints load errors instead of raising ("syntax error",
        # "error: …") — Netmiko passes them through silently, which is exactly
        # how a bad keyword once truncated a line and committed a PARTIAL
        # config. Reject the whole push if any line was refused.
        err = _JUNOS_LOAD_ERR_RE.search(out or "")
        if err:
            raise ValueError(f"junos rejected a config line: {err.group(0).strip()}")
        out += conn.commit(comment="spane config push", read_timeout=120)
        return out
    except Exception:
        try:
            if conn.check_config_mode():
                conn.send_command("rollback 0", expect_string=r"#", read_timeout=30)
        except Exception:  # noqa: BLE001 — best-effort cleanup, original error wins
            logger.warning("junos rollback 0 after failed push/commit also failed",
                           exc_info=True)
        raise
    finally:
        try:
            if conn.check_config_mode():
                conn.exit_config_mode()
        except Exception:  # noqa: BLE001 — never mask the real outcome on exit
            pass


def push_template_to_device(template, device, variables: dict, request) -> dict:
    """Render ``template`` for ``device`` and push it. Returns a per-device result.

    Never raises — failures are captured in the result's ``error`` and audited.
    """
    # Platform gate: a template scoped to a platform won't push to a mismatch.
    if template.platform and device.platform != template.platform:
        error = f"platform mismatch (template {template.platform}, device {device.platform})"
        audit_push(template, device, request, False, error)
        return _result(device, False, error)

    try:
        rendered = render_template(template.template_content, device, variables)
    except Exception as exc:
        error = safe_render_error(exc, template, device)
        audit_push(template, device, request, False, error)
        return _result(device, False, error)

    lines = render_to_lines(rendered)
    if not lines:
        error = "no pushable commands after rendering"
        audit_push(template, device, request, False, error)
        return _result(device, False, error)

    profile = device.credential_profile
    if not profile or not profile.ssh_enabled:
        error = "device has no SSH credential profile"
        audit_push(template, device, request, False, error)
        return _result(device, False, error)

    creds = vault.read_secret(profile.vault_path) if profile.vault_path else {}

    try:
        from netmiko import ConnectHandler

        from apps.compliance.collector import netmiko_device_type
        dtype = netmiko_device_type(device.vendor, device.platform)
        if dtype == "autodetect":
            dtype = "cisco_ios"
        conn = ConnectHandler(
            device_type=dtype, host=str(device.management_ip or device.ip_address),
            username=profile.ssh_username, password=creds.get("ssh_password", ""),
            port=profile.ssh_port or 22, fast_cli=False,
        )
    except Exception as exc:
        logger.warning("config-template connect failed for %s: %s", device.hostname, exc, exc_info=True)
        audit_push(template, device, request, False, "connection failed")
        return _result(device, False, "SSH connection failed")

    try:
        # Junos needs its own flow: isolated private candidate + explicit
        # commit (see push_junos_private). Everything else applies lines
        # immediately, so the generic send_config_set is complete on its own.
        if dtype.startswith("juniper"):
            try:
                push_junos_private(conn, lines)
            except Exception as exc:
                logger.warning("config-template push/commit on %s failed: %s",
                               device.hostname, exc)
                audit_push(template, device, request, False, "push/commit failed")
                return _result(device, False,
                               "push/commit failed — config not applied (candidate discarded)")
        else:
            try:
                conn.send_config_set(lines, read_timeout=30)
            except Exception as exc:
                logger.warning("config-template push to %s failed: %s", device.hostname, exc)
                audit_push(template, device, request, False, "push failed")
                return _result(device, False, "push failed")
    finally:
        try:
            conn.disconnect()
        except Exception:
            pass

    audit_push(template, device, request, True)
    return _result(device, True)


def safe_render_error(exc: Exception, template, device) -> str:
    """Log the render error privately; return a short, safe message for the client."""
    logger.warning("config-template render failed (template=%s device=%s): %s",
                   template.pk, device.hostname, exc)
    return "template render failed"
