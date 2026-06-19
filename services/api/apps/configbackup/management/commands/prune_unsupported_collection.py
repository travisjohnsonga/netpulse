"""Clean up config-collection artifacts for devices that can't have configs.

Deletes ConfigCollectionLog rows for cloud/controller-managed platforms (UniFi/
Mist APs, controllers, switches) whose configuration the platform never collects,
so the Collection Health dashboard reflects only collectable devices. Optionally
removes this stack's own Docker-container "devices" that discovery may have
enrolled (``--delete-docker-devices``).

Idempotent and safe to re-run.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Prune config-collection logs for wireless/cloud-managed devices."

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete-docker-devices", action="store_true",
            help="Also delete Device rows whose hostname is a Docker container name.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would be deleted without deleting.")

    def handle(self, *args, **opts):
        from apps.compliance.collector import SKIP_CONFIG_PLATFORMS
        from apps.configbackup.models import ConfigCollectionLog
        from apps.devices.management.commands.run_discovery import is_infra_hostname
        from apps.devices.models import Device

        dry = opts["dry_run"]

        skip_devices = Device.objects.filter(platform__in=SKIP_CONFIG_PLATFORMS)
        log_qs = ConfigCollectionLog.objects.filter(device__in=skip_devices)
        n_logs = log_qs.count()
        if not dry:
            log_qs.delete()
        self.stdout.write(
            f"{'[dry-run] ' if dry else ''}deleted {n_logs} collection log(s) "
            f"for {skip_devices.count()} wireless/cloud-managed device(s)")

        if opts["delete_docker_devices"]:
            docker = [d for d in Device.objects.all() if is_infra_hostname(d.hostname)]
            for d in docker:
                self.stdout.write(f"  {'would delete' if dry else 'deleting'} "
                                  f"Docker device: {d.hostname} ({d.management_ip or d.ip_address})")
                if not dry:
                    d.delete()
            self.stdout.write(
                f"{'[dry-run] ' if dry else ''}removed {len(docker)} Docker-container device(s)")
