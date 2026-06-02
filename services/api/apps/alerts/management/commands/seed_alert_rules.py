"""
Management command: seed_alert_rules

Seeds the canonical set of default ("system") AlertRules — one per alert the
NetPulse engines actually emit. They show up on Settings → Alerting → Alert
Rules immediately on a fresh install, can be toggled on/off (a disabled rule
suppresses its alerts — see _db_write_alert / interface_monitor), and are
protected from deletion.

Idempotent and non-destructive: an existing rule keeps its user-set severity,
cooldown and is_active state — we only ensure is_system=True and backfill a
blank description. Safe to run on every deploy (called from the api entrypoint).

The rule names below MUST match the rule_name the engines publish, so the
seeded row and the emitted event share one AlertRule:
  - "Interface State Change"     interface_monitor (up/down)
  - "device-unreachable"         run_reachability_monitor (TCP/22)
  - "service-check-failed"       run_check_engine (http/tcp/icmp/... probes)
  - "flow-threshold-exceeded"    stream-processor NetFlow/sFlow volume
  - "latency-threshold-exceeded" stream-processor path latency
  - "log-anomaly-detected"       stream-processor syslog/trap keywords

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
]


class Command(BaseCommand):
    help = "Seed the default (system) alert rules — idempotent, non-destructive."

    def handle(self, *args, **options):
        from apps.alerts.models import AlertRule

        created, marked = [], []
        for name, severity, description, condition, cooldown in DEFAULT_RULES:
            rule, was_created = AlertRule.objects.get_or_create(
                name=name,
                defaults={
                    "severity": severity,
                    "description": description,
                    "condition": condition,
                    "cooldown_minutes": cooldown,
                    "is_system": True,
                    "is_active": True,
                },
            )
            if was_created:
                created.append(name)
                continue
            # Existing rule (possibly auto-created by an engine before seeding):
            # adopt it as a system rule and backfill description, but leave the
            # user's severity / cooldown / is_active alone.
            updates = []
            if not rule.is_system:
                rule.is_system = True
                updates.append("is_system")
            if not rule.description:
                rule.description = description
                updates.append("description")
            if updates:
                rule.save(update_fields=[*updates, "updated_at"])
                marked.append(name)

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created rules: {', '.join(created)}"))
        if marked:
            self.stdout.write(f"Adopted existing as system: {', '.join(marked)}")
        if not created and not marked:
            self.stdout.write("All default alert rules already present.")
