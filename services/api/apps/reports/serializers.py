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

    class Meta:
        model = ReportSchedule
        fields = [
            "id", "report_type", "report_type_display", "frequency", "hour",
            "day_of_week", "day_of_month", "fmt", "recipients", "parameters",
            "enabled", "last_run", "last_status",
        ]
        read_only_fields = ["last_run", "last_status"]


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


REPORT_TYPES = {"compliance-summary": ReportType.COMPLIANCE_SUMMARY,
                "daily-ops": ReportType.DAILY_OPS}
