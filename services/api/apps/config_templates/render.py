"""Jinja2 rendering + variable analysis for config-push templates.

Uses a SandboxedEnvironment (templates are authored by admins, but the sandbox
blocks attribute/builtin abuse and keeps rendering side-effect free). The render
context exposes ``device``, ``site`` and ``settings`` automatically, plus any
admin-supplied/default variables at the top level.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from jinja2 import meta
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

# Variable name fragments that mark a value as a secret.
SENSITIVE_TOKENS = ("pass", "key", "secret", "token", "cred")

# Provided automatically by build_context — never treated as user variables.
AUTO_VARS = {"device", "site", "settings"}

# Global settings exposed as {{ settings.<name> }}, sourced from SystemSetting.
SETTINGS_KEYS = (
    "syslog_server", "syslog_port", "ntp_primary", "ntp_secondary",
    "snmp_community", "dns_primary", "dns_secondary", "domain_suffix",
)


def is_sensitive(name: str) -> bool:
    """True if a variable name looks like it holds a secret."""
    lowered = (name or "").lower()
    return any(token in lowered for token in SENSITIVE_TOKENS)


def _env() -> SandboxedEnvironment:
    return SandboxedEnvironment(
        autoescape=False, trim_blocks=True, lstrip_blocks=True,
        keep_trailing_newline=False,
    )


def detect_variables(template_content: str) -> list[str]:
    """Undeclared variables referenced by the template (excludes auto vars).

    Returns an empty list if the template fails to parse (the caller surfaces
    the syntax error elsewhere).
    """
    try:
        ast = _env().parse(template_content or "")
    except Exception:
        return []
    found = meta.find_undeclared_variables(ast)
    return sorted(v for v in found if v not in AUTO_VARS)


def build_settings_context() -> dict:
    """Global {{ settings.* }} values from the SystemSetting key/value store."""
    from apps.core.models import SystemSetting
    return {key: SystemSetting.get(key, "") for key in SETTINGS_KEYS}


def _device_namespace(device):
    site = getattr(device, "site", None)
    role = getattr(device, "role", None)
    return SimpleNamespace(
        hostname=device.hostname,
        management_ip=str(device.management_ip or device.ip_address or ""),
        ip_address=str(device.ip_address or ""),
        platform=device.platform,
        vendor=device.vendor or "",
        site=SimpleNamespace(name=getattr(site, "name", "") if site else ""),
        role=SimpleNamespace(name=getattr(role, "name", "") if role else ""),
    )


def build_context(device, variables: dict | None) -> dict:
    """Render context: user variables at the top level + auto device/site/settings."""
    ctx = dict(variables or {})
    device_ns = _device_namespace(device)
    ctx["device"] = device_ns
    ctx["site"] = device_ns.site
    ctx["settings"] = SimpleNamespace(**build_settings_context())
    return ctx


def render_template(template_content: str, device, variables: dict | None) -> str:
    """Render the template for a device. Raises on Jinja2 syntax/render errors."""
    tmpl = _env().from_string(template_content or "")
    return tmpl.render(**build_context(device, variables))


def mask_sensitive_output(rendered: str, variables: dict | None) -> str:
    """Replace sensitive variable values in rendered text with a mask token.

    Used for previews so secrets are never returned to the browser.
    """
    out = rendered
    for key, value in (variables or {}).items():
        if is_sensitive(key) and value:
            out = out.replace(str(value), "●●●●●●")
    return out


def render_to_lines(rendered: str) -> list[str]:
    """Pushable config lines: non-blank, ``#`` comments stripped, ASCII-safe.

    ``!`` lines are intentionally kept — they are valid config on Cisco-style
    platforms (and banner delimiters), not comments to drop.
    """
    lines: list[str] = []
    for raw in rendered.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Drop non-ASCII so a mojibake variable can't corrupt the device config.
        lines.append(raw.encode("ascii", "ignore").decode("ascii"))
    return [line for line in lines if line.strip()]
