"""Run the credential secret-broker NATS service.

The broker answers `netpulse.secrets.fetch.*`. The caller's account is taken from
the SUBJECT — NATS injects the importing account at a fixed token position
(account_token_position on the service export), so it is the authenticated
transport identity and cannot be forged in the body. The handler passes that
account (never anything from the payload) to apps.collectors.secret_broker.fetch.

Refuses to start in production without its least-privilege AppRole (fail closed).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand

logger = logging.getLogger("collectors.secret_broker")

# netpulse.secrets.fetch.<ACCOUNT> — the account is the LAST token, injected by
# NATS via the export's account_token_position. We subscribe the wildcard.
SUBJECT = "netpulse.secrets.fetch.*"


def account_from_subject(subject: str) -> str:
    """The transport-authenticated account NATS injected into the subject.

    `netpulse.secrets.fetch.<ACCOUNT>` → <ACCOUNT>. Returns "" if absent so the
    broker denies (never falls back to a body field for identity).
    """
    parts = (subject or "").split(".")
    return parts[3] if len(parts) >= 4 else ""


class Command(BaseCommand):
    help = "Run the credential secret-broker (NATS request/reply over the leaf)."

    def add_arguments(self, parser):
        parser.add_argument("--once-drain", action="store_true",
                            help="for tests: process pending then exit")

    def handle(self, *args, **opts):
        from apps.collectors.secret_broker import check_broker_config
        check_broker_config()  # fail closed before serving
        asyncio.run(self._serve())

    async def _serve(self):
        import nats

        url = os.environ.get("NATS_URL", "nats://nats:4222")
        opts = {"servers": url}
        creds = os.environ.get("BROKER_NATS_CREDS")
        if creds:
            opts["user_credentials"] = creds
        nc = await nats.connect(**opts)
        logger.info("secret-broker connected to %s; serving %s", url, SUBJECT)
        await nc.subscribe(SUBJECT, cb=self._handle)
        stop = asyncio.Event()
        try:
            await stop.wait()
        finally:
            await nc.drain()

    async def _handle(self, msg):
        from apps.collectors.secret_broker import fetch

        account = account_from_subject(msg.subject)        # IDENTITY: transport only
        try:
            body = json.loads(msg.data.decode() or "{}")
        except (ValueError, AttributeError):
            body = {}
        result = await sync_to_async(fetch, thread_sensitive=True)(account, body)
        if msg.reply:
            await msg.respond(json.dumps(result).encode())
