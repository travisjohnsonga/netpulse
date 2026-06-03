"""
reset_test_data — clear all application data while keeping auth users, groups
and permissions. Used by scripts/factory-reset.sh --soft for fast dev cycles.

Each model is deleted independently and best-effort: a model that doesn't exist
(feature not built yet) is skipped rather than aborting the whole reset.
"""
from django.core.management.base import BaseCommand

# (app_label, ModelName) pairs to wipe. Order is not important — FK cascades
# handle dependents. Auth (users/groups/permissions) is intentionally excluded.
_TARGETS = [
    ("devices", "TopologyLink"),
    ("devices", "DiscoveredDevice"),
    ("devices", "DiscoveryJob"),
    ("telemetry", "MonitoredInterface"),
    ("telemetry", "TelemetryConfig"),
    ("configbackup", "DeviceConfig"),
    ("compliance", "ComplianceResult"),
    ("compliance", "CompliancePolicyRule"),
    ("compliance", "CompliancePolicy"),
    ("alerts", "AlertEvent"),
    ("alerts", "AlertRule"),
    ("alerts", "AlertChannel"),
    ("checks", "CheckResult"),
    ("checks", "ServiceCheck"),
    ("cve", "DeviceCVE"),
    ("cve", "CVE"),
    ("lifecycle", "LifecycleMilestone"),
    ("security", "DeviceRiskScore"),
    ("collectors", "Collector"),
    ("devices", "Device"),
    ("credentials", "CredentialProfile"),
    ("devices", "Site"),
    ("devices", "DeviceGroup"),
]


class Command(BaseCommand):
    help = "Delete all application data (devices, telemetry config, alerts, checks, …) but keep auth users/groups."

    def handle(self, *args, **options):
        from django.apps import apps as django_apps

        # Delete each credential profile's OpenBao secret BEFORE the bulk row
        # delete below. The ORM ``.delete()`` bypasses the API's perform_destroy
        # (which calls vault.delete_secret), so without this the secrets would be
        # orphaned in OpenBao. Because vault_path is keyed on a reusable pk, a
        # newly-created profile would then read the stale secret back — the root
        # cause of "credentials revert to placeholder values" after a reset.
        try:
            from apps.credentials import vault
            from apps.credentials.models import CredentialProfile

            for cp in CredentialProfile.objects.exclude(vault_path="").only("vault_path"):
                try:
                    vault.delete_secret(cp.vault_path)
                except Exception as exc:  # pragma: no cover - best effort
                    self.stderr.write(f"  skip vault cleanup for {cp.vault_path}: {exc}")
        except Exception as exc:  # pragma: no cover - defensive
            self.stderr.write(f"  credential vault cleanup skipped: {exc}")

        total = 0
        for app_label, model_name in _TARGETS:
            try:
                model = django_apps.get_model(app_label, model_name)
            except LookupError:
                continue
            try:
                n, _ = model.objects.all().delete()
            except Exception as exc:  # pragma: no cover - defensive
                self.stderr.write(f"  skip {app_label}.{model_name}: {exc}")
                continue
            if n:
                self.stdout.write(f"  cleared {app_label}.{model_name}: {n}")
                total += n
        self.stdout.write(self.style.SUCCESS(f"Test data cleared ({total} rows). Auth users/groups kept."))
