"""Link an Agent to a ``devices.Device`` row, guaranteeing one exists.

An agent's identity is its mTLS cert + hostname, NOT its IP — but ``Device``
requires a unique ``ip_address``. Agents enroll/report THROUGH nginx, so
``REMOTE_ADDR`` is the proxy's shared IP; the first agent claimed a Device with
it and every later agent hit "IP already owned" and ended up **device-less**
(no site assignment, no device-scoped metrics/alerts). This module fixes that:
prefer the real client IP (``get_client_ip``, spoof-resistant), and when that's
missing or already taken, fall back to a **unique per-agent synthetic ULA** so an
active agent is NEVER device-less. Shared by enroll, the metrics handler
(self-heals existing orphans on next check-in), and the site-change action.
"""
from __future__ import annotations

from apps.core.client_ip import get_client_ip


def placeholder_ip(agent) -> str:
    """A unique, valid, non-routable IPv6 (fd00::/8 ULA) derived from the agent's
    UUID — used when no usable routable IP is available so device creation (which
    needs a unique ip_address) always succeeds. Clearly a placeholder."""
    h = agent.id.hex  # 32 hex chars from the UUID
    s = "fd" + h[2:]  # keep it in fc00::/7 ULA space; still 32 hex chars
    return ":".join(s[i:i + 4] for i in range(0, 32, 4))


def ensure_agent_device(agent, request=None, site=None):
    """Guarantee the agent has a linked Device, creating/claiming one if missing.
    Returns the Device (or None only if the agent has no hostname, which can't
    happen for an enrolled agent). Idempotent: a no-op when already linked."""
    from apps.devices.models import Device
    from .models import Agent

    if agent.device_id:
        return agent.device

    device = Device.objects.filter(hostname=agent.hostname).first()
    if device is None:
        ip = get_client_ip(request) if request is not None else None
        # Real client IP only if it's usable AND not already owned; else a unique
        # synthetic ULA so the unique+required ip_address constraint can't block
        # device creation (agents behind one NAT share a public IP).
        if not ip or Device.objects.filter(ip_address=ip).exists():
            ip = placeholder_ip(agent)
        device = Device.objects.create(
            hostname=agent.hostname, ip_address=ip, management_ip=ip,
            platform=Device.Platform.OTHER, status=Device.Status.ACTIVE,
            site=site, notes="Monitored by spane Agent",
        )
    elif site is not None and not device.site_id:
        device.site = site
        device.save(update_fields=["site"])

    # device.agent is a OneToOne — transfer it off any prior agent (e.g. a revoked
    # enrollment for this host) so the (re-)enrolling agent can claim it.
    prior = Agent.objects.filter(device=device).exclude(pk=agent.pk).first()
    if prior:
        prior.device = None
        prior.save(update_fields=["device", "updated_at"])
    agent.device = device
    agent.save(update_fields=["device", "updated_at"])
    return device
