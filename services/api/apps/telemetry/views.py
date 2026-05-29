from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def metrics_stub(request):
    return Response({"detail": "Telemetry metrics API — not yet implemented."}, status=501)
