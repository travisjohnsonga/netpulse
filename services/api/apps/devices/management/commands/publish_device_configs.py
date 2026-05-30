import logging

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Publish all active SNMP devices to NATS (netpulse.devices.upsert) for the poller."

    def handle(self, *args, **options):
        from apps.devices import snmp_publish

        # Publish even when the per-save flag is off (this command is explicit).
        from django.conf import settings
        if not getattr(settings, "SNMP_DEVICE_PUBLISH", True):
            settings.SNMP_DEVICE_PUBLISH = True

        count = snmp_publish.publish_all_active()
        msg = f"published {count} device config(s) to {snmp_publish.UPSERT_SUBJECT}"
        logger.info("publish_device_configs: %s", msg)
        self.stdout.write(self.style.SUCCESS(msg))
