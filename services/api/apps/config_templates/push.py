"""Render a template for a device and push it over SSH (Netmiko).

Reuses the same connection path as telemetry config push: resolve the device's
credential profile, read the SSH password from OpenBao, pick a Netmiko
device_type from vendor/platform, and ``send_config_set`` the rendered lines.
Every attempt is audit-logged; raw exceptions are logged server-side and never
returned to the client.
"""

from __future__ import annotations

import logging

from apps.credentials import vault

from .render import render_template, render_to_lines

logger = logging.getLogger(__name__)


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
