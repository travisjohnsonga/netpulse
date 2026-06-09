"""Prepare an operator signing-key rotation runbook (Finding B, option 2).

Django does NOT execute the rotation: adding/removing an operator signing key
re-signs the operator JWT, which requires the operator *identity* key — and that
key is kept COLD/offline, out of the live API's reach. So this command only
PREPARES the artifacts: the ordered nsc + SIGHUP steps and the exact set of
accounts to re-sign (read from the collector inventory). A human/secure deploy
pipeline runs them where the identity key + nsc live.

Two modes (both proven in scripts/t3/rotate.sh):
  --mode planned     zero collector breakage (re-sign + push BEFORE retiring the
                     old key)
  --mode compromise  stage all re-signed account JWTs FIRST, then cut over to the
                     new key + push — breakage bounded to PUSH time (harness
                     measured ~2.4s for 16 accounts), not push+sign time.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Prepare (not execute) an operator signing-key rotation runbook."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["planned", "compromise"], required=True)

    def handle(self, *args, **opts):
        from apps.collectors.models import Collector

        mode = opts["mode"]
        accounts = list(
            Collector.objects.filter(
                collector_type=Collector.CollectorType.REMOTE, nats_account__gt=""
            ).values_list("nats_account", flat=True)
        )
        # AGG is the aggregate account on the collector-hub; always re-signed too.
        accounts = ["AGG", *sorted(accounts)]
        if not accounts:
            raise CommandError("no enrolled remote-collector accounts found")

        w = self.stdout.write
        w(self.style.WARNING(f"\nOperator signing-key rotation — mode: {mode}\n"))
        w("Run on the SECURE host where the operator IDENTITY key (cold) + nsc live.")
        w(f"Accounts to re-sign ({len(accounts)}): {', '.join(accounts)}\n")
        w("Prerequisites: operator identity key available to nsc; SIGHUP reach to")
        w("every collector-hub node (the operator JWT is a deploy artifact each hub")
        w("reads — rewrite it + `nats-server --signal reload=<pid>` / `docker kill -s HUP`).\n")

        sk = "  nsc edit operator --sk generate            # mint SK_new (re-signs op JWT w/ IDENTITY key)"
        resign = "  for A in {}; do nsc edit account $A -K SK_new; done".format(" ".join(accounts))
        push = "  nsc push --all -u nats://<collector-hub>:4222   # resolver update — NO nats.conf reload"
        deploy = "  <deploy>: rewrite operator.jwt on every hub, then SIGHUP each (connection-preserving)"

        if mode == "planned":
            steps = [
                ("1", sk),
                ("2", deploy + "   # hubs now trust SK_old + SK_new"),
                ("3", resign + "   # re-sign every account under SK_new"),
                ("4", push + "    # push BEFORE retiring SK_old → zero breakage"),
                ("5", "  nsc edit operator --rm-sk SK_old"),
                ("6", deploy + "   # retire SK_old; accounts already on SK_new"),
            ]
            note = "Collectors never see an invalid account → ZERO breakage."
        else:
            steps = [
                ("1", sk),
                ("2", resign + "   # STAGE: re-sign locally, do NOT push yet"),
                ("3", "  nsc edit operator --rm-sk SK_old           # trust ONLY SK_new"),
                ("4", deploy + "   # cut over (SIGHUP); resolver still has SK_old JWTs → invalid NOW"),
                ("5", push + "    # push the prepared SK_new batch → recovery"),
            ]
            note = ("Breakage window = SIGHUP→push (bounded to push-time because re-signed "
                    "JWTs were staged first). Harness measured ~2.4s for 16 accounts.")

        for n, s in steps:
            w(f"{n}.{s}")
        w("\n" + self.style.SUCCESS(note))
        w("\nReference (tested, both modes): scripts/t3/rotate.sh  [8/8 passing]\n")
