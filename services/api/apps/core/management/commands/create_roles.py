"""
Management command: create_roles

Seeds the four NetPulse role-group objects (Admin, Engineer, Viewer, API).
Safe to run on every deploy — idempotent.

Usage:
    python manage.py create_roles
    python manage.py create_roles --superuser admin  # also promotes <username> to admin role + is_staff
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


ROLES = ["Admin", "Engineer", "Viewer", "API"]


class Command(BaseCommand):
    help = "Seed the four NetPulse role groups (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--superuser",
            metavar="USERNAME",
            help="Promote an existing user to is_superuser + is_staff + Admin role.",
        )

    def handle(self, *args, **options):
        from django.contrib.auth.models import Group

        created, existed = [], []
        for name in ROLES:
            _, was_created = Group.objects.get_or_create(name=name)
            (created if was_created else existed).append(name)

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created groups: {', '.join(created)}"))
        if existed:
            self.stdout.write(f"Already existed: {', '.join(existed)}")

        username = options.get("superuser")
        if username:
            User = get_user_model()
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"User '{username}' not found."))
                return
            user.is_superuser = True
            user.is_staff     = True
            user.role         = "admin"
            user.save(update_fields=["is_superuser", "is_staff", "role"])
            admin_group, _ = Group.objects.get_or_create(name="Admin")
            user.groups.add(admin_group)
            self.stdout.write(
                self.style.SUCCESS(f"User '{username}' promoted to superuser + Admin role.")
            )
