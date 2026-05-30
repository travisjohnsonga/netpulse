import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import certs
from .models import ServerCertificate
from .serializers import (
    CSRRequestSerializer,
    CSRResponseSerializer,
    SelfSignedRequestSerializer,
    ServerCertificateStatusSerializer,
    UploadCertificateSerializer,
)

logger = logging.getLogger(__name__)


def _sync_model(meta: dict, source: str) -> None:
    obj = ServerCertificate.load()
    obj.common_name = meta.get("common_name", "") or ""
    obj.issuer = meta.get("issuer", "") or ""
    obj.sans = meta.get("sans", []) or []
    obj.serial = meta.get("serial", "") or ""
    obj.fingerprint_sha256 = meta.get("fingerprint_sha256", "") or ""
    obj.not_before = meta.get("not_before")
    obj.not_after = meta.get("not_after")
    obj.source = source
    obj.installed = True
    obj.save()


def _status_payload() -> dict:
    st = certs.current_status()
    st["source"] = ServerCertificate.load().source if st["installed"] else ""
    return st


class SSLStatusView(APIView):
    """Current HTTPS server certificate status (metadata + expiry; no secrets)."""

    @extend_schema(responses=ServerCertificateStatusSerializer, summary="HTTPS certificate status")
    def get(self, request):
        return Response(ServerCertificateStatusSerializer(_status_payload()).data)


class SSLSelfSignedView(APIView):
    """Generate and install a self-signed HTTPS certificate."""

    @extend_schema(request=SelfSignedRequestSerializer, responses=ServerCertificateStatusSerializer,
                   summary="Generate a self-signed certificate")
    def post(self, request):
        req = SelfSignedRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        cert_pem = certs.generate_self_signed(
            req.validated_data["common_name"], req.validated_data["sans"], req.validated_data["days"]
        )
        _sync_model(certs.parse_cert(cert_pem), ServerCertificate.Source.SELF_SIGNED)
        logger.info("ssl: %s generated self-signed cert CN=%s",
                    getattr(request.user, "username", "?"), req.validated_data["common_name"])
        return Response(ServerCertificateStatusSerializer(_status_payload()).data, status=status.HTTP_201_CREATED)


class SSLCSRView(APIView):
    """Generate a CSR (GET returns the pending CSR; POST creates a new key+CSR)."""

    @extend_schema(responses=CSRResponseSerializer, summary="Get the pending CSR")
    def get(self, request):
        st = certs.current_status()
        if not st["pending_csr"]:
            return Response({"detail": "no pending CSR"}, status=status.HTTP_404_NOT_FOUND)
        return Response(CSRResponseSerializer({"csr": st["pending_csr"]}).data)

    @extend_schema(request=CSRRequestSerializer, responses=CSRResponseSerializer,
                   summary="Generate a private key + CSR")
    def post(self, request):
        req = CSRRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        csr = certs.generate_csr(
            req.validated_data["common_name"], req.validated_data["sans"],
            req.validated_data["organization"], req.validated_data["country"],
        )
        logger.info("ssl: %s generated CSR CN=%s",
                    getattr(request.user, "username", "?"), req.validated_data["common_name"])
        return Response(CSRResponseSerializer({"csr": csr}).data, status=status.HTTP_201_CREATED)


class SSLUploadView(APIView):
    """Upload a CA-signed (or any) certificate, optionally with its private key."""

    @extend_schema(request=UploadCertificateSerializer, responses=ServerCertificateStatusSerializer,
                   summary="Upload a certificate")
    def post(self, request):
        req = UploadCertificateSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        had_csr = bool(certs.current_status()["pending_csr"])
        try:
            cert_pem = certs.install_uploaded(
                req.validated_data["certificate"],
                req.validated_data.get("private_key") or None,
                req.validated_data.get("chain") or None,
            )
        except certs.CertError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        # CSR-fulfilling uploads are CA-signed; a bundled key is a direct upload.
        source = ServerCertificate.Source.CSR if had_csr and not req.validated_data.get("private_key") \
            else ServerCertificate.Source.UPLOADED
        _sync_model(certs.parse_cert(cert_pem), source)
        logger.info("ssl: %s uploaded certificate (source=%s)",
                    getattr(request.user, "username", "?"), source)
        return Response(ServerCertificateStatusSerializer(_status_payload()).data)
