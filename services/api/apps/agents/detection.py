"""Server-role auto-detection for agents.

Matches the services an agent reports running (``Agent.reported_services``,
populated from metrics) against the built-in ``ServerRole`` service lists, so the
UI can suggest roles to assign. Pure logic (no I/O beyond the ServerRole query),
so it's easy to unit test.
"""
from __future__ import annotations

from .models import AgentRole, ServerRole


def auto_detect_roles(agent) -> list[dict]:
    """Return candidate roles for ``agent`` based on its reported running services.

    Each entry: ``{role_id, role_name, role_type, matched_services, confidence,
    assigned}`` where confidence = matched / total role services (0–1).
    """
    running = {s for s in (agent.reported_services or []) if s}
    if not running:
        return []
    assigned_ids = set(
        AgentRole.objects.filter(agent=agent).values_list("role_id", flat=True)
    )
    is_windows = (agent.os or "").lower().startswith("win")

    detected: list[dict] = []
    for role in ServerRole.objects.filter(is_builtin=True):
        svcs = set(role.windows_services if is_windows else role.linux_services)
        if not svcs:
            continue
        matches = running & svcs
        if not matches:
            continue
        detected.append({
            "role_id": role.id,
            "role_name": role.name,
            "role_type": role.role_type,
            "matched_services": sorted(matches),
            "confidence": round(len(matches) / max(len(svcs), 1), 2),
            "assigned": role.id in assigned_ids,
        })
    detected.sort(key=lambda d: d["confidence"], reverse=True)
    return detected
