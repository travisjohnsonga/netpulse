from rest_framework import serializers

from .models import GeneratedReport, ReportSchedule, ReportType
from .schedule_tz import local_to_utc, utc_to_local


class GeneratedReportSerializer(serializers.ModelSerializer):
    title = serializers.CharField(read_only=True)
    report_type_display = serializers.CharField(source="get_report_type_display", read_only=True)
    generated_by_username = serializers.CharField(source="generated_by.username", read_only=True, default=None)

    class Meta:
        model = GeneratedReport
        fields = [
            "id", "report_type", "report_type_display", "title", "generated_at",
            "generated_by_username", "source", "parameters", "file_size", "format",
        ]


class ReportScheduleSerializer(serializers.ModelSerializer):
    report_type_display = serializers.CharField(source="get_report_type_display", read_only=True)
    delivery_display = serializers.CharField(source="get_delivery_display", read_only=True)

    class Meta:
        model = ReportSchedule
        fields = [
            "id", "report_type", "report_type_display", "frequency", "hour",
            "day_of_week", "day_of_month", "fmt", "delivery", "delivery_display",
            "recipients", "parameters", "enabled", "last_run", "last_status",
            "timezone",
        ]
        read_only_fields = ["last_run", "last_status", "timezone"]

    # ── Timezone boundary (see apps.reports.schedule_tz) ──────────────────────
    # hour/day_* are stored in UTC; the user enters/sees them in their own tz.
    timezone = serializers.SerializerMethodField()

    def _tz_name(self) -> str:
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            from apps.core.models import UserPreferences
            return UserPreferences.for_user(user).timezone or "UTC"
        return "UTC"

    def get_timezone(self, obj) -> str:
        """The tz the output hour/day fields are expressed in (the requester's)."""
        return self._tz_name()

    def validate_day_of_week(self, value):
        if not 0 <= value <= 6:
            raise serializers.ValidationError("Day of week must be 0 (Mon) … 6 (Sun).")
        return value

    def validate_day_of_month(self, value):
        # Capped at 28 so monthly/quarterly schedules never skip a short month.
        if not 1 <= value <= 28:
            raise serializers.ValidationError("Day of month must be 1–28.")
        return value

    def validate(self, attrs):
        """Recipients are required only when email delivery is selected; a
        store-only schedule emails nobody and needs no recipients. Then convert
        the user-local hour/day fields to the UTC values actually stored."""
        delivery = attrs.get("delivery") or getattr(
            self.instance, "delivery", None) or ReportSchedule.Delivery.EMAIL
        # On PATCH, fall back to the stored recipients when not being changed.
        if "recipients" in attrs:
            recipients = attrs["recipients"]
        else:
            recipients = getattr(self.instance, "recipients", None) or []
        recipients = [r for r in (recipients or []) if r]
        if delivery in (ReportSchedule.Delivery.EMAIL, ReportSchedule.Delivery.BOTH) and not recipients:
            raise serializers.ValidationError(
                {"recipients": "At least one recipient is required when email delivery is selected."})

        # The cadence day field must be provided for the frequencies that use it:
        # weekly needs day_of_week; monthly/quarterly need day_of_month. (Daily
        # ignores both.) Checked on the SUBMITTED values, before tz conversion.
        frequency = attrs.get("frequency") or getattr(
            self.instance, "frequency", None) or ReportSchedule.Frequency.DAILY
        F = ReportSchedule.Frequency
        has_dow = "day_of_week" in attrs or getattr(self.instance, "day_of_week", None) is not None
        has_dom = "day_of_month" in attrs or getattr(self.instance, "day_of_month", None) is not None
        if frequency == F.WEEKLY and not has_dow:
            raise serializers.ValidationError(
                {"day_of_week": "Day of week is required for a weekly schedule (0=Mon … 6=Sun)."})
        if frequency in (F.MONTHLY, F.QUARTERLY) and not has_dom:
            raise serializers.ValidationError(
                {"day_of_month": f"Day of month is required for a {frequency} schedule (1–28)."})

        # Convert the submitted local time → UTC for storage. Only when the time
        # is part of this write (create always sends hour; PATCH may omit it and
        # leave the stored UTC value untouched).
        if "hour" in attrs:
            dow = attrs.get("day_of_week", getattr(self.instance, "day_of_week", 0))
            dom = attrs.get("day_of_month", getattr(self.instance, "day_of_month", 1))
            h, w, m = local_to_utc(attrs["hour"], dow, dom, self._tz_name())
            attrs["hour"] = h
            attrs["day_of_week"] = w
            attrs["day_of_month"] = m
        return attrs

    def to_representation(self, instance):
        """Convert the stored UTC hour/day fields back to the requester's tz."""
        data = super().to_representation(instance)
        h, w, m = utc_to_local(
            instance.hour, instance.day_of_week, instance.day_of_month, self._tz_name())
        data["hour"] = h
        data["day_of_week"] = w
        data["day_of_month"] = m
        return data


class ComplianceSummaryRequestSerializer(serializers.Serializer):
    format = serializers.ChoiceField(choices=["pdf", "csv", "json"], default="pdf")
    group_by = serializers.ListField(
        child=serializers.ChoiceField(choices=["site", "role", "platform"]),
        required=False)
    site_ids = serializers.ListField(child=serializers.IntegerField(), required=False)
    include_score_breakdown = serializers.BooleanField(default=True)
    as_of = serializers.DateTimeField(required=False, allow_null=True)


class DailyOpsRequestSerializer(serializers.Serializer):
    format = serializers.ChoiceField(choices=["pdf", "csv", "html", "json"], default="pdf")
    date = serializers.DateField(required=False, allow_null=True)
    site_ids = serializers.ListField(child=serializers.IntegerField(), required=False)


class OpsReportRequestSerializer(serializers.Serializer):
    """Operations report over a reporting period (daily/weekly/monthly/quarterly)."""
    period = serializers.ChoiceField(
        choices=["daily", "weekly", "monthly", "quarterly"], default="daily")
    format = serializers.ChoiceField(choices=["pdf", "csv", "html", "json"], default="pdf")
    end_date = serializers.DateField(required=False, allow_null=True)
    site_ids = serializers.ListField(child=serializers.IntegerField(), required=False)


REPORT_TYPES = {"compliance-summary": ReportType.COMPLIANCE_SUMMARY,
                "daily-ops": ReportType.DAILY_OPS}
