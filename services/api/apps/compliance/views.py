from rest_framework import viewsets
from rest_framework.mixins import ListModelMixin, RetrieveModelMixin
from rest_framework.viewsets import GenericViewSet

from .models import CompliancePolicy, CompliancePolicyRule, ComplianceResult
from .serializers import CompliancePolicyRuleSerializer, CompliancePolicySerializer, ComplianceResultSerializer


class CompliancePolicyViewSet(viewsets.ModelViewSet):
    queryset = CompliancePolicy.objects.prefetch_related("rules").all()
    serializer_class = CompliancePolicySerializer
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class CompliancePolicyRuleViewSet(viewsets.ModelViewSet):
    queryset = CompliancePolicyRule.objects.select_related("policy").all()
    serializer_class = CompliancePolicyRuleSerializer
    filterset_fields = ["policy", "check_type", "is_active"]


class ComplianceResultViewSet(ListModelMixin, RetrieveModelMixin, GenericViewSet):
    queryset = ComplianceResult.objects.select_related("device", "policy", "rule").all()
    serializer_class = ComplianceResultSerializer
    filterset_fields = ["device", "policy", "outcome"]
    ordering_fields = ["created_at"]
