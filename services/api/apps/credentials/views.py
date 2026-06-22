from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.permissions import AdminOnly

from .models import CredentialProfile, PROTOCOL_LABELS
from .serializers import CredentialProfileListSerializer, CredentialProfileSerializer
from . import probe, vault

# How each protocol maps onto the reachability probe.
_PROBE_TYPE = {
    "ssh": "ssh_password",
    "snmpv2c": "snmpv2c",
    "snmpv3": "snmpv3",
    "https": "http_basic",
    "netconf": "netconf",
    "gnmi": "gnmi",
}


class CredentialProfileViewSet(viewsets.ModelViewSet):
    """
    Manage multi-protocol credential profiles (SSH, SNMPv2c/v3, HTTPS, NETCONF, gNMI).

    A profile enables one or more protocols and stores all their secret material
    together in OpenBao — never in the database, never echoed on read. Filter by
    enabled protocol (e.g. `ssh_enabled=true`); search by name. Extra actions:
    `test/?ip=` probes every enabled protocol against an IP and records the
    outcome; `devices/` lists the devices using the profile.
    """

    queryset = CredentialProfile.objects.all()
    filterset_fields = [
        "ssh_enabled", "snmpv2c_enabled", "snmpv3_enabled",
        "https_enabled", "netconf_enabled", "gnmi_enabled", "last_test_result",
    ]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "last_tested", "created_at"]

    # Creating/editing/deleting a profile writes (and on delete, removes) secret
    # material in OpenBao — admin-only. Reads (list/retrieve) and the operational
    # actions (test/ connectivity probe, devices/ listing) stay on the default
    # permission so engineers can still see profiles and run probes.
    # (Track 2 replaces this hardcoded AdminOnly with a credential:manage capability.)
    _ADMIN_ACTIONS = frozenset({"create", "update", "partial_update", "destroy"})

    def get_permissions(self):
        if self.action in self._ADMIN_ACTIONS:
            return [AdminOnly()]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "list":
            return CredentialProfileListSerializer
        return CredentialProfileSerializer

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        profile = serializer.save(created_by=user)
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        # Never log secret values — only the profile name/protocols.
        log_event(AuditLog.EventType.CREDENTIAL_CREATED, request=self.request, target=profile,
                  description=f'Credential profile "{profile.name}" created')

    def perform_update(self, serializer):
        profile = serializer.save()
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        # Secret material lives in OpenBao and never reaches the audit record.
        log_event(AuditLog.EventType.CREDENTIAL_UPDATED, request=self.request, target=profile,
                  description=f'Credential profile "{profile.name}" updated')

    def perform_destroy(self, instance):
        from apps.core.audit import log_event
        from apps.core.models import AuditLog
        name = instance.name
        vault.delete_secret(instance.vault_path)
        log_event(AuditLog.EventType.CREDENTIAL_DELETED, request=self.request, target=instance,
                  description=f'Credential profile "{name}" deleted')
        instance.delete()

    @action(detail=True, methods=["post"], url_path="test")
    def test(self, request, pk=None):
        """
        Probe every enabled protocol against ``?ip=x.x.x.x``. Returns a per-protocol
        result list plus an overall verdict, and records it on the profile.
        """
        ip = request.query_params.get("ip")
        if not ip:
            return Response({"detail": "Query parameter 'ip' is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        profile = self.get_object()
        protocols = profile.enabled_protocols
        if not protocols:
            return Response({"detail": "No protocols are enabled on this profile."},
                            status=status.HTTP_400_BAD_REQUEST)

        results = []
        for proto in protocols:
            r = probe.probe(_PROBE_TYPE[proto], ip, profile.port_for(proto), False)
            results.append({
                "protocol": proto,
                "label": PROTOCOL_LABELS[proto],
                "success": r["success"],
                "message": r["message"],
                "port": r["port"],
            })

        n_ok = sum(1 for r in results if r["success"])
        if n_ok == len(results):
            overall = CredentialProfile.TestResult.SUCCESS
        elif n_ok == 0:
            overall = CredentialProfile.TestResult.FAILURE
        else:
            overall = CredentialProfile.TestResult.PARTIAL

        profile.last_tested = timezone.now()
        profile.last_test_result = overall
        profile.last_test_message = "; ".join(
            f"{r['label']}: {'ok' if r['success'] else 'fail'}" for r in results
        )
        profile.save(update_fields=["last_tested", "last_test_result", "last_test_message"])

        return Response({"ip": ip, "overall": overall, "results": results})

    @action(detail=True, methods=["get"], url_path="devices")
    def devices(self, request, pk=None):
        """List devices assigned to this credential profile."""
        profile = self.get_object()
        devices = profile.devices.all()
        return Response([
            {"id": d.id, "hostname": d.hostname, "ip_address": d.ip_address, "status": d.status}
            for d in devices
        ])
