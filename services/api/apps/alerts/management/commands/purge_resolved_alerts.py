"""Delete resolved AlertEvents older than a retention window (default 90 days)."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Purge resolved alerts older than --days (default 90)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)

    def handle(self, *args, **options):
        n = purge_resolved_alerts(options["days"])
        self.stdout.write(self.style.SUCCESS(f"Purged {n} resolved alerts older than {options['days']} days."))


def purge_resolved_alerts(days: int = 90) -> int:
    """Delete RESOLVED events resolved more than `days` ago. Returns count deleted."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.alerts.models import AlertEvent

    cutoff = timezone.now() - timedelta(days=days)
    qs = AlertEvent.objects.filter(state=AlertEvent.State.RESOLVED, resolved_at__lt=cutoff)
    deleted, _ = qs.delete()
    return deleted
