from rest_framework import serializers

from .models import GeneratedReport, ReportSchedule, ReportType


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
        ]
        read_only_fields = ["last_run", "last_status"]

    def validate(self, attrs):
        """Recipients are required only when email delivery is selected; a
        store-only schedule emails nobody and needs no recipients."""
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
        return attrs


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
