import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.errors import safe_detail
from apps.core.permissions import AdminOnly
from . import ca_store, certs
from .models import CACertificate, ServerCertificate
from .serializers import (
    CACertificateSerializer,
    CACertificateUploadSerializer,
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

    permission_classes = [AdminOnly]   # mutates server cert/key — admin-only

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

    permission_classes = [AdminOnly]   # CSR generation/retrieval = admin cert mgmt

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

    permission_classes = [AdminOnly]   # installs server cert/key — admin-only

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
            return Response({"detail": safe_detail(exc, logger, "install certificate",
                            public="The certificate could not be installed — it may be malformed, "
                                   "unsupported, or may not match the private key.")},
                            status=status.HTTP_400_BAD_REQUEST)
        # CSR-fulfilling uploads are CA-signed; a bundled key is a direct upload.
        source = ServerCertificate.Source.CSR if had_csr and not req.validated_data.get("private_key") \
            else ServerCertificate.Source.UPLOADED
        _sync_model(certs.parse_cert(cert_pem), source)
        logger.info("ssl: %s uploaded certificate (source=%s)",
                    getattr(request.user, "username", "?"), source)
        return Response(ServerCertificateStatusSerializer(_status_payload()).data)


# ── Trusted CA certificates ───────────────────────────────────────────────────


def _decode_upload(raw_text: str) -> bytes:
    """Turn the uploaded text into bytes: PEM stays text; otherwise try base64 DER."""
    text = raw_text.strip()
    if "-----BEGIN" in text:
        return text.encode()
    import base64
    try:
        return base64.b64decode(text, validate=True)
    except Exception:
        return text.encode()


class CACertificateListView(APIView):
    """List trusted CA certificates (GET) or add one/many (POST)."""

    # Adding a trusted CA changes the trust store — admin-only (POST); the GET
    # list stays on the default permission.
    def get_permissions(self):
        return [AdminOnly()] if self.request.method == "POST" else super().get_permissions()

    @extend_schema(responses=CACertificateSerializer(many=True), summary="List trusted CA certificates")
    def get(self, request):
        qs = CACertificate.objects.all()
        return Response(CACertificateSerializer(qs, many=True).data)

    @extend_schema(request=CACertificateUploadSerializer, responses=CACertificateSerializer(many=True),
                   summary="Add a trusted CA certificate (PEM/DER/PKCS#7)")
    def post(self, request):
        req = CACertificateUploadSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        try:
            parsed = ca_store.load_certificates(_decode_upload(req.validated_data["certificate"]))
        except ca_store.CAError as exc:
            return Response({"detail": safe_detail(exc, logger, "add CA certificate",
                            public="The CA certificate could not be parsed (invalid or unsupported format).")},
                            status=status.HTTP_400_BAD_REQUEST)

        added, skipped = [], []
        for cert in parsed:
            meta = ca_store.parse_metadata(cert)
            obj, created = CACertificate.objects.get_or_create(
                fingerprint_sha256=meta["fingerprint_sha256"],
                defaults={
                    "name": req.validated_data.get("name") or meta["subject"],
                    "subject": meta["subject"], "issuer": meta["issuer"],
                    "not_before": meta["not_before"], "not_after": meta["not_after"],
                    "cert_pem": meta["cert_pem"], "is_root": meta["is_root"],
                    "is_intermediate": meta["is_intermediate"],
                    "added_by": request.user if request.user.is_authenticated else None,
                },
            )
            (added if created else skipped).append(obj)

        ca_store.rebuild_bundle()
        logger.info("ca-trust: %s added %d CA cert(s) (%d duplicate(s) skipped)",
                    getattr(request.user, "username", "?"), len(added), len(skipped))
        return Response(CACertificateSerializer(CACertificate.objects.all(), many=True).data,
                        status=status.HTTP_201_CREATED if added else status.HTTP_200_OK)


class CACertificateDetailView(APIView):
    """Delete a trusted CA certificate and rebuild the bundle."""

    permission_classes = [AdminOnly]   # removing a trusted CA — admin-only

    @extend_schema(summary="Delete a trusted CA certificate")
    def delete(self, request, pk):
        from rest_framework.generics import get_object_or_404
        ca = get_object_or_404(CACertificate, pk=pk)
        name = ca.name
        ca.delete()
        ca_store.rebuild_bundle()
        logger.info("ca-trust: %s deleted CA cert %s", getattr(request.user, "username", "?"), name)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CACertificateVerifyView(APIView):
    """Verify a stored CA cert is currently valid (dates) and report expiry."""

    @extend_schema(summary="Verify a trusted CA certificate")
    def post(self, request, pk):
        from rest_framework.generics import get_object_or_404
        ca = get_object_or_404(CACertificate, pk=pk)
        st, days = ca_store.expiry_status(ca.not_after)
        valid = st not in ("expired", "none")
        return Response({"valid": valid, "expiry_status": st, "days_remaining": days})
