from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response


@extend_schema(
    summary="Telemetry metrics (not yet implemented)",
    description="Placeholder for the telemetry query API. Time-series data lives in "
                "InfluxDB; this endpoint returns 501 until the query layer ships.",
    responses={501: inline_serializer("NotImplemented", {"detail": serializers.CharField()})},
)
@api_view(["GET"])
def metrics_stub(request):
    return Response({"detail": "Telemetry metrics API — not yet implemented."}, status=501)
