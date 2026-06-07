"""
Management command: ensure_superuser

Idempotently create the initial superuser from the DJANGO_SUPERUSER_* env vars.
Only creates the user when it does not already exist — existing users (and any
password the operator later changed) are left untouched. Safe to run on every
api container start; intended to run ONLY in the api service.

Env:
    DJANGO_SUPERUSER_USERNAME  (required)
    DJANGO_SUPERUSER_PASSWORD  (required)
    DJANGO_SUPERUSER_EMAIL     (optional)
"""
from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import IntegrityError


class Command(BaseCommand):
    help = "Idempotently create the initial superuser from DJANGO_SUPERUSER_* env vars."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")

        if not username or not password:
            self.stdout.write("DJANGO_SUPERUSER_USERNAME/PASSWORD not set — skipping superuser seed.")
            return

        User = get_user_model()
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' already exists — leaving it unchanged.")
            return

        try:
            User.objects.create_superuser(
                username=username, email=email, password=password, role="admin",
                # The initial admin starts on the fixed default password and must
                # change it on first login (enforced by the SPA + change-password API).
                must_change_password=True,
            )
        except IntegrityError:
            # Lost a create race with another process — fine, the user now exists.
            self.stdout.write(f"Superuser '{username}' created concurrently — skipping.")
            return

        self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}' (role=admin)."))
