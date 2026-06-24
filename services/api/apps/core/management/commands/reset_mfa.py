"""Break-glass: clear a user's MFA from the server console.

Recovery path for a locked-out account (lost authenticator device) — including
the immutable superadmin, who is always MFA-required and so cannot otherwise be
rescued through the API. Run on the host with stack access; the action is
audit-logged. It does NOT reveal the secret; it removes it.

    docker compose exec api python manage.py reset_mfa <username>
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Break-glass: reset (clear) a user's MFA so they can re-enroll."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Username whose MFA to reset.")

    def handle(self, *args, **opts):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog

        username = opts["username"]
        user = User.objects.filter(username=username).first()
        if user is None:
            raise CommandError(f"No such user: {username!r}")

        device = getattr(user, "mfa_device", None)
        had_mfa = bool(device and device.mfa_enabled)
        if device is not None:
            device.clear()
            device.save()

        # No request/actor (console action). Audited so the break-glass use is
        # visible; never logs the secret.
        log_event(
            AuditLog.EventType.MFA_RESET_BY_ADMIN, user=None, username="(console)",
            target=user, description=f"MFA reset via console break-glass for {username}",
            metadata={"had_mfa": had_mfa, "via": "management_command"},
        )
        self.stdout.write(self.style.SUCCESS(
            f"MFA reset for {username} (had_mfa={had_mfa}). "
            "They will be prompted to re-enroll on next login if required."))
