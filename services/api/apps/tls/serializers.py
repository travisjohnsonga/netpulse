from rest_framework import serializers


class ServerCertificateStatusSerializer(serializers.Serializer):
    """Read-only status of the installed HTTPS server certificate. No secrets."""

    installed = serializers.BooleanField()
    has_private_key = serializers.BooleanField()
    source = serializers.CharField(required=False, allow_blank=True)
    common_name = serializers.CharField(allow_blank=True)
    issuer = serializers.CharField(allow_blank=True)
    sans = serializers.ListField(child=serializers.CharField(), default=list)
    serial = serializers.CharField(allow_blank=True)
    fingerprint_sha256 = serializers.CharField(allow_blank=True)
    not_before = serializers.DateTimeField(allow_null=True)
    not_after = serializers.DateTimeField(allow_null=True)
    expiry_status = serializers.CharField()
    days_remaining = serializers.IntegerField(allow_null=True)
    pending_csr = serializers.CharField(allow_null=True)


class SelfSignedRequestSerializer(serializers.Serializer):
    common_name = serializers.CharField(max_length=255)
    sans = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    days = serializers.IntegerField(required=False, default=825, min_value=1, max_value=3650)


class CSRRequestSerializer(serializers.Serializer):
    common_name = serializers.CharField(max_length=255)
    sans = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    organization = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    country = serializers.CharField(max_length=2, required=False, allow_blank=True, default="")


class CSRResponseSerializer(serializers.Serializer):
    csr = serializers.CharField()


class UploadCertificateSerializer(serializers.Serializer):
    certificate = serializers.CharField(help_text="PEM-encoded certificate")
    # write_only: a supplied private key is stored on disk, never echoed back.
    private_key = serializers.CharField(required=False, allow_blank=True, write_only=True,
                                        help_text="PEM private key (omit to reuse the CSR's key)")
    chain = serializers.CharField(required=False, allow_blank=True,
                                  help_text="Optional intermediate chain PEM")
