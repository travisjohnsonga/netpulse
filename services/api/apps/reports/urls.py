from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    ComplianceScheduleView,
    ComplianceSummaryView,
    DailyOpsScheduleView,
    DailyOpsView,
    GeneratedReportViewSet,
    ReportScheduleViewSet,
)

router = DefaultRouter()
router.register("schedules", ReportScheduleViewSet, basename="report-schedule")
# History + download live at the collection root (/api/reports/, /api/reports/{id}/download/).
router.register("", GeneratedReportViewSet, basename="report")

urlpatterns = [
    path("compliance-summary/", ComplianceSummaryView.as_view(), name="report-compliance-summary"),
    path("compliance-summary/schedule/", ComplianceScheduleView.as_view(), name="report-compliance-schedule"),
    path("daily-ops/", DailyOpsView.as_view(), name="report-daily-ops"),
    path("daily-ops/schedule/", DailyOpsScheduleView.as_view(), name="report-daily-ops-schedule"),
] + router.urls
