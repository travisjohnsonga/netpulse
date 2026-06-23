import logging

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.errors import internal_error_response
from apps.core.permissions import CapabilityViewSetMixin

from .models import WanCircuit
from .serializers import WanCircuitSerializer

logger = logging.getLogger(__name__)


class WanCircuitViewSet(CapabilityViewSetMixin, viewsets.ModelViewSet):
    """CRUD for WAN circuits + a per-circuit utilization endpoint.

    Filter by ``?site`` / ``?device`` / ``?circuit_type`` / ``?status``.
    """

    view_capability = "circuit:view"
    write_capability = "circuit:edit"
    queryset = WanCircuit.objects.select_related("device", "site").all()
    serializer_class = WanCircuitSerializer
    filterset_fields = ["site", "device", "circuit_type", "status"]
    search_fields = ["name", "circuit_id", "provider"]
    ordering_fields = ["name", "monthly_cost", "contract_end_date", "created_at"]

    @action(detail=True, methods=["get"])
    def utilization(self, request, pk=None):
        """Current/24h/peak/95th-percentile utilization of the bound interface."""
        circuit = self.get_object()
        period = request.query_params.get("period", "24h")
        try:
            from .utilization import get_circuit_utilization
            data = get_circuit_utilization(circuit, period=period)
        except Exception as exc:  # noqa: BLE001
            return internal_error_response(exc, logger, f"circuit utilization {circuit.pk}")
        if data is None:
            return Response({
                "circuit_id": circuit.id, "name": circuit.name,
                "bound": False,
                "detail": "Bind a device + interface to see utilization.",
            })
        return Response({**data, "bound": True})
