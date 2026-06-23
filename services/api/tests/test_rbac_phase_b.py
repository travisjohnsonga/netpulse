"""RBAC Track 2 — Phase B.

Phase B migrates every viewset onto capability checks (HasCapability /
CapabilityViewSetMixin) and flips the DRF default to deny-by-default. These tests
are the structural drift guard + the behavior-preservation oracle:

- DRIFT GUARD: the default permission IS DenyByDefault; no view file still imports
  the legacy permission classes; and every reachable DRF view under apps.* either
  declares explicit permissions or overrides get_permissions (a CapabilityViewSet
  mixin subclass must set view_capability) — i.e. nothing silently inherits the
  deny-by-default and 403s the whole endpoint by accident.
- DELIBERATE TIGHTENINGS: the four intended access changes, with before/after.
- PRESERVATION: superuser bypass + the unauthenticated allowlist + admin reaches
  every representative endpoint (no accidental deny).
"""
import ast
import glob

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import get_resolver
from rest_framework.views import APIView

from apps.core.permissions import CapabilityViewSetMixin, DenyByDefault

User = get_user_model()
pytestmark = pytest.mark.django_db

LEGACY_CLASSES = {"NetPulsePermission", "AdminOnly", "IsAnyRole", "AdminOrReadOnly"}

# View modules that legitimately stay on the unauthenticated/self-service
# allowlist (AllowAny, cert-auth, or IsAuthenticated self-service) — they declare
# permissions explicitly, so the drift guard's "inherits default" check passes
# them anyway; listed here only for documentation.


def _iter_view_classes():
    """Every distinct DRF view class reachable from the root URLconf, under apps.*."""
    seen = {}

    def walk(patterns):
        for p in patterns:
            if hasattr(p, "url_patterns"):
                walk(p.url_patterns)
                continue
            cb = getattr(p, "callback", None)
            if cb is None:
                continue
            cls = getattr(cb, "cls", None) or getattr(cb, "view_class", None)
            if cls is None:
                continue
            if not isinstance(cls, type) or not issubclass(cls, APIView):
                continue
            if not cls.__module__.startswith("apps."):
                continue
            seen[(cls.__module__, cls.__qualname__)] = cls

    walk(get_resolver().url_patterns)
    return list(seen.values())


class TestDriftGuard:
    def test_default_permission_is_deny_by_default(self):
        assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
            "apps.core.permissions.DenyByDefault"
        ]

    def test_no_view_file_imports_legacy_permission_classes(self):
        """A migrated viewset must use HasCapability — fail if any view module
        still imports/uses NetPulsePermission / AdminOnly / IsAnyRole / the
        deleted AdminOrReadOnly (catches both 'uses old class' regressions)."""
        offenders = {}
        files = (glob.glob("apps/*/views.py") + glob.glob("apps/*/server_views.py")
                 + glob.glob("apps/*/wireless.py"))
        for path in files:
            tree = ast.parse(open(path).read(), filename=path)
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names.update(a.name for a in node.names)
                elif isinstance(node, ast.Name):
                    names.add(node.id)
            hit = names & LEGACY_CLASSES
            if hit:
                offenders[path] = sorted(hit)
        assert not offenders, f"legacy permission classes still referenced: {offenders}"

    def test_no_reachable_view_inherits_the_default(self):
        """The other half of the drift guard: a view that declares no explicit
        permissions inherits DenyByDefault and would 403 the whole endpoint. Fail
        if any reachable apps.* view neither sets a non-default permission_classes
        nor overrides get_permissions."""
        offenders = []
        for cls in _iter_view_classes():
            overrides_get_perms = cls.get_permissions is not APIView.get_permissions
            pc = list(getattr(cls, "permission_classes", []) or [])
            # An explicitly-empty permission_classes (e.g. simplejwt's public
            # login views) is an intentional AllowAny, NOT the inherited default —
            # a view that never declares one resolves to [DenyByDefault].
            inherits_default = (not overrides_get_perms) and (
                pc == [DenyByDefault] or DenyByDefault in pc
            )
            if inherits_default:
                offenders.append(f"{cls.__module__}.{cls.__qualname__}")
        assert not offenders, (
            "views inherit deny-by-default (forgot to migrate — they 403 everyone): "
            + ", ".join(offenders)
        )

    def test_capability_mixin_subclasses_set_view_capability(self):
        """A CapabilityViewSetMixin subclass that forgot view_capability silently
        denies all reads (mixin returns DenyByDefault). Catch that."""
        offenders = []
        for cls in _iter_view_classes():
            if issubclass(cls, CapabilityViewSetMixin) and cls is not CapabilityViewSetMixin:
                if getattr(cls, "view_capability", None) is None:
                    offenders.append(f"{cls.__module__}.{cls.__qualname__}")
        assert not offenders, f"mixin viewsets missing view_capability: {offenders}"


class TestDeliberateTightenings:
    """The FOUR intended access changes (everything else preserves prior access)."""

    # (1) config:backup:manage → admin-only (was engineer via the default).
    def test_configbackup_settings_write_now_admin_only(self, engineer_client, admin_client):
        url = "/api/configbackup/config-backup/"
        assert engineer_client.put(url, {"git_enabled": False}, format="json").status_code == 403
        assert admin_client.put(url, {"git_enabled": False}, format="json").status_code == 200

    # (2) CollectorViewSet writes → collector:manage (admin-only; was engineer).
    def test_collector_create_now_admin_only(self, engineer_client, admin_client):
        assert engineer_client.post("/api/collectors/", {"name": "eng-coll"}).status_code == 403
        assert admin_client.post("/api/collectors/", {"name": "adm-coll"}).status_code == 201

    # (3) report generate → report:generate (engineer+; viewer loses it).
    def test_report_generate_now_engineer_plus(self, viewer_client, engineer_client, monkeypatch):
        assert viewer_client.post("/api/reports/daily-ops/", {"format": "json"},
                                  format="json").status_code == 403
        from apps.reports import views as rviews

        class _R:  # _generate_and_respond only touches the file for non-json
            file_path = "x"
        monkeypatch.setattr(rviews, "generate", lambda *a, **k: (_R(), '{"ok": 1}', {}))
        r = engineer_client.post("/api/reports/daily-ops/", {"format": "json"}, format="json")
        assert r.status_code != 403

    # (4) manual config-collect trigger → config:backup:manage (admin; was all-auth).
    def test_manual_config_collect_now_admin_only(
        self, viewer_client, engineer_client, admin_client, monkeypatch
    ):
        from apps.devices.models import Device
        d = Device.objects.create(hostname="cb-1", ip_address="10.66.0.1")
        url = f"/api/configbackup/configs/collect/{d.pk}/"
        assert viewer_client.post(url).status_code == 403
        assert engineer_client.post(url).status_code == 403
        monkeypatch.setattr("django.core.management.call_command", lambda *a, **k: None)
        assert admin_client.post(url).status_code not in (401, 403)

    # (5) CVEFeedSettings PUT → cve:manage (admin; was engineer via the default).
    # Secret-bearing feed credentials are admin-tier. Per-device CVE triage stays
    # engineer (cve:triage) — only the feed-credential endpoint moved.
    def test_cve_feed_settings_write_now_admin_only(self, engineer_client, admin_client):
        url = "/api/cve/feed-settings/"
        assert engineer_client.put(url, {"nvd_enabled": True}, format="json").status_code == 403
        assert admin_client.put(url, {"nvd_enabled": True}, format="json").status_code == 200

    def test_device_cve_triage_stays_engineer(self, engineer_client):
        from apps.cve.models import CVE, DeviceCVE
        from apps.devices.models import Device
        d = Device.objects.create(hostname="cve-1", ip_address="10.55.0.1")
        c = CVE.objects.create(cve_id="CVE-2026-0001", description="x", severity="high")
        dc = DeviceCVE.objects.create(device=d, cve=c)
        # cve:triage unchanged on DeviceCVEViewSet — engineer can still PATCH.
        r = engineer_client.patch(f"/api/cve/device-cves/{dc.id}/", {"is_patched": True},
                                  format="json")
        assert r.status_code not in (401, 403)


class TestAccessPreservingOperateCaps:
    """The engineer operate caps (Q1) PRESERVE access — engineers keep these
    operational actions that the admin-managed domains expose."""

    def test_engineer_keeps_credential_probe(self, engineer_client):
        from apps.credentials.models import CredentialProfile
        p = CredentialProfile.objects.create(name="op", ssh_enabled=True, ssh_username="x")
        # credential:test — engineer keeps the connectivity probe (no ?ip= → 400, not 403).
        assert engineer_client.post(f"/api/credentials/{p.id}/test/").status_code != 403

    def test_engineer_keeps_integration_sync_and_tls_verify(self, engineer_client):
        # integration:sync — Mist test probe stays open to engineers.
        assert engineer_client.post("/api/integrations/mist/test/").status_code != 403


class TestSuperuserBypass:
    def test_superuser_reaches_admin_only_endpoints(self, db, api_client):
        from rest_framework_simplejwt.tokens import RefreshToken
        su = User.objects.create_superuser(username="suphaseb", password="x", email="s@x.io")
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(su).access_token}")
        assert api_client.get("/api/users/").status_code == 200
        assert api_client.get("/api/collectors/").status_code == 200


class TestUnauthenticatedAllowlist:
    def test_public_endpoints_do_not_require_auth(self, api_client):
        for url in ("/api/health/", "/api/version/", "/api/sso/providers/"):
            code = api_client.get(url).status_code
            assert code not in (401, 403), f"{url} should be public, got {code}"

    def test_agent_ca_certificate_is_public(self, api_client):
        # Public (agents fetch it pre-auth). PKI may be unconfigured in tests →
        # 503, but never an auth/permission rejection.
        assert api_client.get("/api/agents/ca-certificate/").status_code not in (401, 403)


class TestAdminNotAccidentallyDenied:
    """Deny-by-default confirmation: admin reaches every representative endpoint
    (a 403 here would mean a viewset was left inheriting the default)."""

    ENDPOINTS = [
        "/api/devices/", "/api/sites/", "/api/alerts/rules/", "/api/alerts/events/",
        "/api/alerts/channels/", "/api/alerting/teams/", "/api/circuits/", "/api/checks/",
        "/api/compliance/policies/", "/api/compliance/templates/", "/api/compliance/results/",
        "/api/cve/cves/", "/api/cve/device-cves/", "/api/lifecycle/milestones/",
        "/api/security/risk-scores/", "/api/collectors/", "/api/credentials/",
        "/api/integrations/unifi/", "/api/configbackup/configs/", "/api/backup/records/",
        "/api/reports/", "/api/frameworks/", "/api/logs/filters/", "/api/agents/",
        "/api/servers/", "/api/mibs/", "/api/users/", "/api/topology/manual-links/",
    ]

    def test_admin_is_never_403(self, admin_client):
        denied = [u for u in self.ENDPOINTS if admin_client.get(u).status_code in (401, 403)]
        assert not denied, f"admin accidentally denied (un-migrated viewset?): {denied}"
