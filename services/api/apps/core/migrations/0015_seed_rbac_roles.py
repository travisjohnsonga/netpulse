"""Seed the five system RBAC roles and point every existing user's rbac_role FK
at the role matching their legacy ``role`` value — reproducing today's behavior
exactly (Phase A is non-breaking; the legacy ``role`` CharField stays the live
enforcement input, and no viewset uses capabilities yet).

The capability sets come from ``apps.core.capabilities`` (code-defined). Note for
Phase B: several viewsets are currently ``IsAuthenticated`` (arp_mac, configbackup,
flows, frameworks, logs, reports) — i.e. any authenticated user can reach them
today. Phase A does NOT tighten those; the seeded role sets reflect intent, and
Phase B must decide whether enforcing the matching capabilities narrows current
access (e.g. report:generate / config:backup:manage for viewer/api).
"""
from django.db import migrations


def seed_roles(apps, schema_editor):
    from apps.core.capabilities import LEGACY_ROLE_TO_SYSTEM, SYSTEM_ROLES

    RBACRole = apps.get_model("core", "RBACRole")
    User = apps.get_model("core", "NetPulseUser")

    by_name = {}
    for spec in SYSTEM_ROLES:
        role, _ = RBACRole.objects.update_or_create(
            name=spec["name"],
            defaults={
                "description": spec["description"],
                # Sorted list for deterministic storage/diffs.
                "capabilities": sorted(spec["capabilities"]),
                "is_system": spec["is_system"],
                "is_immutable": spec["is_immutable"],
            },
        )
        by_name[spec["name"]] = role

    # Point each existing user at the system role matching their legacy role.
    for user in User.objects.all():
        target = by_name.get(LEGACY_ROLE_TO_SYSTEM.get(user.role))
        if target is not None and user.rbac_role_id != target.id:
            user.rbac_role = target
            user.save(update_fields=["rbac_role"])


def unseed_roles(apps, schema_editor):
    RBACRole = apps.get_model("core", "RBACRole")
    # Users' rbac_role is SET_NULL, so deleting the system roles clears the FKs.
    RBACRole.objects.filter(is_system=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_rbacrole_and_user_fk"),
    ]

    operations = [
        migrations.RunPython(seed_roles, unseed_roles),
    ]
