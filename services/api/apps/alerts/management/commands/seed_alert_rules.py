"""
Management command: seed_alert_rules

Seeds the canonical set of default ("system") AlertRules — one per alert the
NetPulse engines actually emit. They show up on Settings → Alerting → Alert
Rules immediately on a fresh install, can be toggled on/off (a disabled rule
suppresses its alerts — see _db_write_alert / interface_monitor), and are
protected from deletion.

Seed-once bootstrap: the defaults are seeded exactly ONCE, on a fresh install.
A durable ``SeedMarker`` (key ``alert_rules``) records that bootstrap happened;
every subsequent boot (the entrypoint re-invokes this) sees the marker and skips
entirely, so an operator's deletions of default rules STICK across restarts.
Upgrade-safe: an existing install with rules but no marker is recognized as
already past bootstrap and marked WITHOUT re-seeding (no surprise duplicates,
existing rules untouched). Safe to run on every deploy.

The rule names below MUST match the rule_name the engines publish, so the
seeded row and the emitted event share one AlertRule:
  - "Interface State Change"     interface_monitor (up/down)
  - "device-unreachable"         run_reachability_monitor (TCP/22)
  - "service-check-failed"       run_check_engine (http/tcp/icmp/... probes)
  - "flow-threshold-exceeded"    stream-processor NetFlow/sFlow volume
  - "latency-threshold-exceeded" stream-processor path latency
  - "log-anomaly-detected"       stream-processor syslog/trap keywords
  - "High Temperature Warning"   stream-processor ENTITY-SENSOR temp ≥ warn
  - "High Temperature Critical"  stream-processor ENTITY-SENSOR temp ≥ crit
  - "Temperature Sensor Failed"  stream-processor sensor oper-status not ok

Usage:
    python manage.py seed_alert_rules
"""
from __future__ import annotations

from django.core.management.base import BaseCommand


# name, severity, description, condition, cooldown_minutes
DEFAULT_RULES = [
    (
        "Interface State Change", "high",
        "Monitored interface up/down transitions.",
        {"rule_type": "interface_state_change"}, 0,
    ),
    (
        "device-unreachable", "critical",
        "Device failed its TCP/22 reachability check.",
        {"source": "reachability_monitor"}, 60,
    ),
    (
        "service-check-failed", "high",
        "An agentless service check is down or degraded.",
        {"source": "check_engine"}, 60,
    ),
    (
        "flow-threshold-exceeded", "high",
        "NetFlow/sFlow volume exceeded the configured threshold.",
        {"source": "stream-processor", "metric": "flow_mbps"}, 60,
    ),
    (
        "latency-threshold-exceeded", "medium",
        "Measured path latency exceeded the configured threshold.",
        {"source": "stream-processor", "metric": "latency_ms"}, 60,
    ),
    (
        "log-anomaly-detected", "medium",
        "Syslog/trap message matched an anomaly keyword.",
        {"source": "stream-processor", "metric": "log_keywords"}, 60,
    ),
    (
        "High Ping Latency", "medium",
        "Device RTT exceeded the warning threshold (default >100ms for 3 checks).",
        {"source": "reachability_monitor", "metric": "rtt_ms"}, 30,
    ),
    (
        "Ping Latency Critical", "high",
        "Device RTT exceeded the critical threshold (default >500ms for 2 checks).",
        {"source": "reachability_monitor", "metric": "rtt_ms"}, 15,
    ),
    (
        "Config Changed", "medium",
        "A scheduled config collection detected a running-config change.",
        {"source": "config_manager", "metric": "config_diff"}, 0,
    ),
    (
        "High Temperature Warning", "medium",
        "A device temperature sensor exceeded the warning threshold (default ≥75°C).",
        {"source": "stream-processor", "metric": "temperature_c"}, 30,
    ),
    (
        "High Temperature Critical", "critical",
        "A device temperature sensor exceeded the critical threshold (default ≥85°C).",
        {"source": "stream-processor", "metric": "temperature_c"}, 15,
    ),
    (
        "Temperature Sensor Failed", "high",
        "A device temperature sensor reported a non-operational status.",
        {"source": "stream-processor", "metric": "sensor_status"}, 60,
    ),
    (
        "High PoE Usage", "medium",
        "A switch's PoE power usage exceeds the configured percentage of its "
        "budget (default ≥80%).",
        {"source": "environment_poll", "metric": "poe_used_pct"}, 0,
    ),
    (
        "High WAN Utilization", "medium",
        "A WAN circuit's utilization exceeded its configured alert threshold.",
        {"source": "circuits", "metric": "wan_utilization"}, 0,
    ),
    (
        "WAN Contract Expiring", "medium",
        "A WAN circuit contract is approaching its end date (90/60/30/14/7 days).",
        {"source": "circuits", "metric": "wan_contract"}, 0,
    ),
]


# Seed-once marker key. Once a SeedMarker with this key exists, this command
# never touches AlertRules again — so operator deletions of default rules stick
# across container reboots (the entrypoint re-invokes this on every boot).
SEED_KEY = "alert_rules"


class Command(BaseCommand):
    help = (
        "Seed the default (system) alert rules ONCE on a fresh install. "
        "Idempotent and upgrade-safe: skips entirely once bootstrapped."
    )

    def handle(self, *args, **options):
        from apps.alerts.models import AlertRule
        from apps.core.models import SeedMarker

        # (1) Already bootstrapped → the operator owns the rules now. Skip
        #     entirely so deleted defaults are never recreated.
        if SeedMarker.is_seeded(SEED_KEY):
            self.stdout.write(
                "Alert rules already bootstrapped (marker present) — skipping seed."
            )
            return

        # (2) No marker but rules already exist → an existing install upgrading
        #     to seed-once. Recognize we're past bootstrap and set the marker,
        #     but DON'T re-seed and DON'T touch the existing rules.
        if AlertRule.objects.exists():
            SeedMarker.mark(
                SEED_KEY,
                note="marked past-bootstrap on upgrade (existing rules present; not re-seeded)",
            )
            self.stdout.write(
                "Existing alert rules present with no seed marker — marked as "
                "bootstrapped WITHOUT re-seeding (upgrade-safe)."
            )
            return

        # (3) Truly fresh install (no marker, no rules) → seed the defaults and
        #     set the marker so this never runs again.
        created = []
        for name, severity, description, condition, cooldown in DEFAULT_RULES:
            # Every DEFAULT_RULE monitors the customer's network/servers, so all
            # seeded defaults are Tier-2 OPERATIONAL. The only Tier-1 SYSTEM rule
            # (the notification-delivery meta-alarm) is created lazily by
            # dispatch.py, not seeded here.
            _rule, was_created = AlertRule.objects.get_or_create(
                name=name,
                defaults={
                    "severity": severity,
                    "description": description,
                    "condition": condition,
                    "cooldown_minutes": cooldown,
                    "kind": AlertRule.Kind.OPERATIONAL,
                    "is_system": True,
                    "is_active": True,
                },
            )
            if was_created:
                created.append(name)

        SeedMarker.mark(SEED_KEY, note=f"fresh-install seed ({len(created)} rules)")
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(created)} default alert rules and set the bootstrap marker."
            )
        )
