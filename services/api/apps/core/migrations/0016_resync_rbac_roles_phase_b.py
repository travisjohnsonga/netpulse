"""RBAC Track 2 Phase B — re-sync the seeded system roles to the current
capability catalog on already-migrated databases.

Phase B extended the catalog (31 → 54 caps) and adjusted the seeded role sets
(new per-viewset caps + engineer operate caps + the five deliberate tightenings).
The Phase-A seed (0015) ran once with the old sets; it will NOT re-run on an
existing deployment, so without this migration a deployed admin/engineer/viewer
role would keep the stale 31-cap sets and 403 on every new-cap endpoint.

This re-runs the same idempotent ``update_or_create`` over ``SYSTEM_ROLES`` (so
each role row's ``capabilities`` are brought up to the current code-defined set)
and back-fills ``rbac_role`` for any user that is missing it or pointed at a
system role that no longer matches their legacy ``role`` — covering users created
in the gap between the Phase-A seed and this deploy (before NetPulseUser.save()
started syncing rbac_role). A user with an explicit custom (non-system) role is
left untouched. On a fresh database this is a harmless no-op after 0015.
"""
from django.db import migrations


def resync_roles(apps, schema_editor):
    from apps.core.capabilities import LEGACY_ROLE_TO_SYSTEM, SYSTEM_ROLES

    RBACRole = apps.get_model("core", "RBACRole")
    User = apps.get_model("core", "NetPulseUser")

    by_name = {}
    for spec in SYSTEM_ROLES:
        role, _ = RBACRole.objects.update_or_create(
            name=spec["name"],
            defaults={
                "description": spec["description"],
                "capabilities": sorted(spec["capabilities"]),
                "is_system": spec["is_system"],
                "is_immutable": spec["is_immutable"],
            },
        )
        by_name[spec["name"]] = role

    # Back-fill / re-sync users whose rbac_role is missing or is a system role
    # that doesn't match their legacy role. Custom (non-system) assignments are
    # respected and never overwritten.
    system_role_ids = {r.id for r in by_name.values()}
    for user in User.objects.all():
        target = by_name.get(LEGACY_ROLE_TO_SYSTEM.get(user.role))
        if target is None:
            continue
        keep_custom = (
            user.rbac_role_id is not None and user.rbac_role_id not in system_role_ids
        )
        if keep_custom:
            continue
        if user.rbac_role_id != target.id:
            user.rbac_role = target
            user.save(update_fields=["rbac_role"])


def noop(apps, schema_editor):
    # Irreversible re-sync: the prior (Phase-A) capability sets aren't recoverable
    # from here, and reversing would leave the current sets in place anyway.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_seed_rbac_roles"),
    ]

    operations = [
        migrations.RunPython(resync_roles, noop),
    ]
