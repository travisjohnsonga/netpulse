from rest_framework import serializers

from .models import AlertChannel, AlertEvent, AlertRule, NotificationLog


class NotificationLogSerializer(serializers.ModelSerializer):
    """A delivery attempt (the operator-facing status/log view)."""
    event_title = serializers.SerializerMethodField()

    class Meta:
        model = NotificationLog
        fields = ("id", "event", "event_title", "channel", "channel_name", "channel_type",
                  "transition", "status", "attempts", "detail", "created_at")
        read_only_fields = fields

    def get_event_title(self, obj):
        return (obj.event.annotations or {}).get("title") or (obj.event.rule.name if obj.event_id else "")


class AlertChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertChannel
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class AlertRuleSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = AlertRule
        fields = "__all__"
        # is_system / kind classify the rule and are set by the seed command,
        # the dispatch meta-alarm, or the backfill migration — never via the API.
        # created_by is provenance — set server-side (clone action), never by a
        # client-supplied value.
        read_only_fields = ("created_at", "updated_at", "is_system", "kind", "created_by")


class AlertEventSerializer(serializers.ModelSerializer):
    rule_name = serializers.CharField(source="rule.name", read_only=True)
    severity = serializers.CharField(source="rule.severity", read_only=True)
    # Convenience fields derived from labels/annotations so the UI doesn't have
    # to dig into the JSON. Interface state-change alerts carry their real
    # (per-event) severity and interface metadata there; see interface_monitor.
    effective_severity = serializers.SerializerMethodField()
    fired_at = serializers.DateTimeField(source="created_at", read_only=True)
    title = serializers.SerializerMethodField()
    message = serializers.SerializerMethodField()
    device = serializers.SerializerMethodField()
    device_id = serializers.SerializerMethodField()
    interface = serializers.SerializerMethodField()
    transition = serializers.SerializerMethodField()
    downtime_seconds = serializers.SerializerMethodField()
    is_interface_alert = serializers.SerializerMethodField()
    is_resolved = serializers.SerializerMethodField()
    is_acknowledged = serializers.SerializerMethodField()
    acknowledged_by = serializers.SerializerMethodField()
    acknowledged_at = serializers.SerializerMethodField()
    # Long-form detail (e.g. a config-change unified diff) + a machine type so the
    # UI can render the expanded panel appropriately.
    details = serializers.SerializerMethodField()
    alert_type = serializers.SerializerMethodField()
    # Subject classification so the UI links to the right detail page:
    # network_device → /devices/{device_id}; server → /servers/{server_id}
    # (the Agent UUID — /servers/:id is keyed by Agent, not Device id).
    device_kind = serializers.SerializerMethodField()
    server_id = serializers.SerializerMethodField()

    class Meta:
        model = AlertEvent
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at", "resolved_at", "resolved_by", "resolution_note")

    def get_is_resolved(self, obj):
        return obj.state == AlertEvent.State.RESOLVED

    def _latest_ack(self, obj):
        # acknowledgements prefetched + ordered -acknowledged_at by the viewset.
        acks = list(obj.acknowledgements.all())
        return acks[0] if acks else None

    def get_is_acknowledged(self, obj):
        return obj.state != AlertEvent.State.RESOLVED and bool(self._latest_ack(obj))

    def get_acknowledged_by(self, obj):
        ack = self._latest_ack(obj)
        return (ack.acknowledged_by.username if ack and ack.acknowledged_by else None)

    def get_acknowledged_at(self, obj):
        ack = self._latest_ack(obj)
        return ack.acknowledged_at.isoformat() if ack else None

    def get_effective_severity(self, obj):
        return (obj.annotations or {}).get("severity") \
            or (obj.labels or {}).get("severity") \
            or obj.rule.severity

    def get_title(self, obj):
        return (obj.annotations or {}).get("title") or obj.rule.name

    def get_message(self, obj):
        return (obj.annotations or {}).get("message") or ""

    def get_details(self, obj):
        return (obj.annotations or {}).get("details") or ""

    def get_alert_type(self, obj):
        return (obj.annotations or {}).get("alert_type") \
            or (obj.labels or {}).get("alert_type") or ""

    def get_device(self, obj):
        # Prefer the explicit "device" label (set by some alert types). Otherwise
        # resolve device_id → the real Device hostname so the Alerts Device column
        # shows a name (e.g. "NetPulseW25Test"/"router1") rather than the raw
        # "device {id}" the frontend would fall back to. Genuinely device-less
        # alerts (e.g. log anomalies) return "" (shown as "–").
        labels = obj.labels or {}
        name = labels.get("device")
        if name:
            return name
        device_id = labels.get("device_id")
        if device_id is not None:
            from apps.devices.models import Device
            hostname = (Device.objects.filter(id=device_id)
                        .values_list("hostname", flat=True).first())
            if hostname:
                return hostname
        return ""

    def get_device_id(self, obj):
        return (obj.labels or {}).get("device_id")

    def _subject_meta(self, obj):
        """Resolve device_id → {kind, server_id} once per event (shared by
        get_device_kind + get_server_id). server_id is the Agent UUID for a
        server subject (else None)."""
        cached = getattr(obj, "_subject_meta_cache", None)
        if cached is not None:
            return cached
        meta = {"kind": "", "server_id": None}
        device_id = (obj.labels or {}).get("device_id")
        if device_id is not None:
            from apps.devices.models import Device
            kind = (Device.objects.filter(id=device_id)
                    .values_list("device_kind", flat=True).first())
            if kind:
                meta["kind"] = kind
                if kind == "server":
                    from apps.agents.models import Agent
                    aid = (Agent.objects.filter(device_id=device_id)
                           .exclude(status=Agent.Status.REVOKED)
                           .values_list("id", flat=True).first())
                    meta["server_id"] = str(aid) if aid else None
        obj._subject_meta_cache = meta
        return meta

    def get_device_kind(self, obj):
        return self._subject_meta(obj)["kind"]

    def get_server_id(self, obj):
        return self._subject_meta(obj)["server_id"]

    def get_interface(self, obj):
        return (obj.labels or {}).get("interface") or ""

    def get_transition(self, obj):
        return (obj.labels or {}).get("transition") or ""

    def get_downtime_seconds(self, obj):
        return (obj.annotations or {}).get("downtime_seconds")

    def get_is_interface_alert(self, obj):
        return (obj.labels or {}).get("source") == "interface_monitor"
