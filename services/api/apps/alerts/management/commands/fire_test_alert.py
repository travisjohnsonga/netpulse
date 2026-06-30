"""
Fire (and optionally resolve) a synthetic AlertEvent end-to-end so the dispatch
layer + notifiers can be verified against real channels in a lab.

    # fire a critical alert, validate delivery, then SELF-CLEAN (default)
    python manage.py fire_test_alert --severity critical --title "Dispatch test"

    # link a one-off rule to a specific channel and fire+resolve it
    python manage.py fire_test_alert --channel 3 --resolve

    # keep the artifacts for debugging (skips teardown)
    python manage.py fire_test_alert --keep

    # purge any leftover smoke/test artifacts and exit
    python manage.py fire_test_alert --purge

The firing event flows through the post_save signal → dispatch; --resolve then
flips it to RESOLVED so the recovery notification path is exercised too.

CONVENTION — verification artifacts must self-clean and be recognizable:
- The synthetic rule is named with the ``__smoke__`` prefix (see ``SMOKE_PREFIX``
  / ``SMOKE_NAME``) so any stray artifact is obviously a test object and trivially
  purgeable. The rule is always ``is_system=False`` (never a platform/system rule).
- Teardown runs by DEFAULT in a ``finally`` block: the synthetic event(s) and the
  smoke rule are deleted after delivery is validated (``--keep`` opts out).
- Teardown is guarded to ``__smoke__``-prefixed, non-system rules ONLY, so it can
  never delete a real operator rule. ``--purge`` sweeps any leftovers from older
  runs. Real channels (linked via ``--channel``) are never deleted — only the M2M
  link goes with the rule.
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

# Ephemeral-namespace convention: every artifact this command creates is named
# with this prefix so it's recognizable as a test object and easy to purge.
SMOKE_PREFIX = "__smoke__"
SMOKE_NAME = f"{SMOKE_PREFIX} dispatch test"
# Names matched by --purge (covers this command's artifacts + the legacy/manual
# verification names so a single sweep cleans historical junk too).
PURGE_PREFIXES = (SMOKE_PREFIX, "__test__")


class Command(BaseCommand):
    help = "Fire a synthetic alert through the dispatch layer (lab/manual test); self-cleans by default."

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
        parser.add_argument("--keep", action="store_true",
                            help="Keep the synthetic rule/event (skip the default self-teardown).")
        parser.add_argument("--settle", type=float, default=4.0,
                            help="Seconds to wait for dispatch/delivery before reading the "
                                 "NotificationLog + tearing down (default 4).")
        parser.add_argument("--purge", action="store_true",
                            help="Delete any leftover __smoke__/__test__ artifacts and exit.")

    # --- teardown helper -----------------------------------------------------
    def _purge(self, AlertRule, *, announce=True):
        """Delete every non-system rule whose name starts with a purge prefix
        (cascades its events + NotificationLog rows). Guarded so it can only ever
        touch the ephemeral test namespace, never a real/system rule."""
        from django.db.models import Q
        q = Q()
        for p in PURGE_PREFIXES:
            q |= Q(name__startswith=p)
        victims = AlertRule.objects.filter(q, is_system=False)
        names = list(victims.values_list("name", flat=True))
        deleted = victims.delete()
        if announce:
            self.stdout.write(self.style.SUCCESS(
                f"Purged {len(names)} test artifact rule(s): {names or '—'} ({deleted})"))
        return names

    def handle(self, *args, **opts):
        from django.utils import timezone

        from apps.alerts.models import AlertChannel, AlertEvent, AlertRule, NotificationLog

        if opts["purge"]:
            self._purge(AlertRule)
            return

        rule, _ = AlertRule.objects.get_or_create(
            name=SMOKE_NAME,
            defaults={"severity": opts["severity"], "condition": {"smoke": True},
                      "is_active": True, "is_system": False},
        )
        event = None
        try:
            if opts["channel"]:
                try:
                    ch = AlertChannel.objects.get(pk=opts["channel"])
                    rule.channels.add(ch)
                    self.stdout.write(f"Linked channel #{ch.pk} ({ch.name}) to the test rule.")
                except AlertChannel.DoesNotExist:
                    self.stderr.write(f"Channel #{opts['channel']} not found.")
                    return

            labels = {"source": "smoke_test", "severity": opts["severity"],
                      "transition": "firing"}
            if opts["device"]:
                labels["device"] = opts["device"]
            event = AlertEvent.objects.create(
                rule=rule, state=AlertEvent.State.FIRING, labels=labels,
                annotations={"title": opts["title"], "message": opts["message"],
                             "severity": opts["severity"], "alert_type": "smoke_test"},
            )
            self.stdout.write(self.style.SUCCESS(
                f"Fired AlertEvent #{event.pk} ({opts['severity']}: {opts['title']})"))

            if opts["resolve"]:
                if opts["resolve_after"] > 0:
                    time.sleep(opts["resolve_after"])
                event.refresh_from_db()
                event.state = AlertEvent.State.RESOLVED
                event.resolved_at = timezone.now()
                event.resolved_by = "smoke_test"
                event.resolution_note = "Resolved by fire_test_alert."
                event.save(update_fields=["state", "resolved_at", "resolved_by",
                                          "resolution_note", "updated_at"])
                self.stdout.write(self.style.SUCCESS(f"Resolved AlertEvent #{event.pk}"))

            # Let on_commit dispatch + the (synchronous) notifier sends settle, then
            # report what was delivered so the run validates before it self-cleans.
            if opts["settle"] > 0:
                time.sleep(opts["settle"])
            delivered = list(NotificationLog.objects.filter(event=event)
                             .values_list("channel_type", "transition", "status"))
            self.stdout.write(f"Delivery log for event #{event.pk}: {delivered or 'none'}")
        finally:
            if opts["keep"]:
                self.stdout.write(self.style.WARNING(
                    f"--keep: leaving artifact rule '{rule.name}' (#{rule.pk}) + event in place."))
            else:
                # Guarded teardown: only ever the ephemeral, non-system smoke rule.
                if rule.name.startswith(PURGE_PREFIXES) and not rule.is_system:
                    deleted = AlertRule.objects.filter(pk=rule.pk, is_system=False).delete()
                    self.stdout.write(self.style.SUCCESS(
                        f"Self-clean: removed smoke rule '{rule.name}' + its event/logs ({deleted})."))
