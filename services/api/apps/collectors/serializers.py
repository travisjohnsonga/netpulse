from rest_framework import serializers

from .models import Collector


class CollectorSerializer(serializers.ModelSerializer):
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    device_count = serializers.IntegerField(source="devices.count", read_only=True)
    is_healthy = serializers.BooleanField(read_only=True)
    # Sites this collector is the default for (Site.default_collector is the
    # single assignment authority — assign via the Site endpoint, read here).
    assigned_site_ids = serializers.SerializerMethodField()
    assigned_site_names = serializers.SerializerMethodField()
    # Whether an mTLS cert has been issued (the key material lives in OpenBao,
    # never here — so we only ever expose presence, not the cert).
    has_cert = serializers.SerializerMethodField()

    class Meta:
        model = Collector
        fields = "__all__"
        # `status` is fully lifecycle-managed (enroll/heartbeat → active, scheduler
        # → offline, the revoke action → revoked); a hand-PATCH would just let
        # human state disagree with reality, so it's read-only here. Everything
        # issued/managed by enrollment/heartbeat/scheduler is likewise read-only.
        read_only_fields = (
            "api_key_issued_at", "cert_serial", "cert_fingerprint_sha256",
            "cert_expires_at", "enrolled_at", "nats_account", "status",
            "last_seen_at", "collector_type", "created_at", "updated_at",
        )
        # The API key hash and the enrollment-token hash are the bootstrap trust
        # root — never serialise them outward. write_only keeps them off reads;
        # validate() below drops them off writes too (they're set only by the
        # enrollment/heartbeat code paths, never by a client payload).
        extra_kwargs = {
            # required=False: these are never supplied by a client (the view sets
            # them) — without it the now-writable api_key_hash would be mandatory.
            "api_key_hash": {"write_only": True, "required": False},
            "enrollment_token_hash": {"write_only": True, "required": False},
        }

    def validate(self, attrs):
        # Defence-in-depth: a client can never set the trust-root hashes.
        attrs.pop("api_key_hash", None)
        attrs.pop("enrollment_token_hash", None)
        return attrs

    def get_assigned_site_ids(self, obj) -> list[int]:
        return list(obj.default_for_sites.values_list("id", flat=True))

    def get_assigned_site_names(self, obj) -> list[str]:
        return list(obj.default_for_sites.values_list("name", flat=True))

    def get_has_cert(self, obj) -> bool:
        return bool(obj.cert_fingerprint_sha256)
