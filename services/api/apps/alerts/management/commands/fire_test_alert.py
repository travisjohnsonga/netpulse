"""
Fire (and optionally resolve) a synthetic AlertEvent end-to-end so the dispatch
layer + notifiers can be verified against real channels in a lab.

    # fire a critical alert to every matching channel
    python manage.py fire_test_alert --severity critical --title "Dispatch test"

    # link a one-off rule to a specific channel and fire+resolve it
    python manage.py fire_test_alert --channel 3 --resolve

The firing event flows through the post_save signal → dispatch; --resolve then
flips it to RESOLVED so the recovery notification path is exercised too.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Fire a synthetic alert through the dispatch layer (lab/manual test)."

    def add_arguments(self, parser):
        parser.add_argument("--severity", default="high",
                            choices=["critical", "high", "medium", "low", "info"])
        parser.add_argument("--title", default="spane dispatch test alert")
        parser.add_argument("--message", default="Synthetic alert fired via fire_test_alert.")
        parser.add_argument("--device", default="", help="Optional device hostname label.")
        parser.add_argument("--channel", type=int, default=None,
                            help="Link the test rule to this AlertChannel id.")
        parser.add_argument("--resolve", action="store_true",
                            help="Also resolve the event after firing (tests recovery).")
        parser.add_argument("--resolve-after", type=float, default=2.0,
                            help="Seconds to wait before resolving (default 2).")

    def handle(self, *args, **opts):
        from django.utils import timezone

        from apps.alerts.models import AlertChannel, AlertEvent, AlertRule

        rule, _ = AlertRule.objects.get_or_create(
            name="Dispatch Test",
            defaults={"severity": opts["severity"], "condition": {"test": True},
                      "is_active": True},
        )
        if opts["channel"]:
            try:
                ch = AlertChannel.objects.get(pk=opts["channel"])
                rule.channels.add(ch)
                self.stdout.write(f"Linked channel #{ch.pk} ({ch.name}) to the test rule.")
            except AlertChannel.DoesNotExist:
                self.stderr.write(f"Channel #{opts['channel']} not found.")
                return

        labels = {"source": "dispatch_test", "severity": opts["severity"],
                  "transition": "firing"}
        if opts["device"]:
            labels["device"] = opts["device"]
        event = AlertEvent.objects.create(
            rule=rule, state=AlertEvent.State.FIRING, labels=labels,
            annotations={"title": opts["title"], "message": opts["message"],
                         "severity": opts["severity"], "alert_type": "dispatch_test"},
        )
        self.stdout.write(self.style.SUCCESS(
            f"Fired AlertEvent #{event.pk} ({opts['severity']}: {opts['title']})"))

        if opts["resolve"]:
            if opts["resolve_after"] > 0:
                time.sleep(opts["resolve_after"])
            event.refresh_from_db()
            event.state = AlertEvent.State.RESOLVED
            event.resolved_at = timezone.now()
            event.resolved_by = "test"
            event.resolution_note = "Resolved by fire_test_alert."
            event.save(update_fields=["state", "resolved_at", "resolved_by",
                                      "resolution_note", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Resolved AlertEvent #{event.pk}"))
