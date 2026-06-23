"""RBAC Track 2, Phase A — capability catalog, roles-as-data, non-breaking seed.

Phase A defines the model and reproduces today's behavior; no viewset uses
capabilities yet. These tests pin the seeded sets, the legacy→system mapping the
data migration applies, the superadmin immutability guards, capability-subset
validation, and the has_capability / HasCapability helpers.
"""
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError

from apps.core import capabilities as caps
from apps.core.models import RBACRole, Role
from apps.core.permissions import HasCapability, has_capability

User = get_user_model()
pytestmark = pytest.mark.django_db

EXPECTED = {
    "superadmin": caps.ALL_CAPABILITIES,
    "admin": caps.ALL_CAPABILITIES,
    "engineer": caps.ENGINEER_CAPABILITIES,
    "api": caps.API_CAPABILITIES,
    "viewer": caps.VIEW_CAPABILITIES,
}


class TestCatalog:
    def test_all_capabilities_count(self):
        # Phase B extended the catalog from the Phase-A 31 with granular per-
        # viewset caps + engineer operate caps (agent:edit, credential:test,
        # integration:sync, tls:verify).
        assert len(caps.ALL_CAPABILITIES) == 54

    def test_subsets_within_all(self):
        for s in (caps.VIEW_CAPABILITIES, caps.ENGINEER_CAPABILITIES, caps.API_CAPABILITIES):
            assert s <= caps.ALL_CAPABILITIES

    def test_view_caps_all_end_in_view(self):
        # chatops:use is the one intentional viewer-tier exception (the in-UI
        # chat is a "use" capability, not a ":view" read).
        assert all(c.endswith(":view") or c == caps.CHATOPS_USE
                   for c in caps.VIEW_CAPABILITIES)


class TestSeededRoles:
    def test_five_system_roles(self):
        names = set(RBACRole.objects.filter(is_system=True).values_list("name", flat=True))
        assert names == {"superadmin", "admin", "engineer", "api", "viewer"}

    @pytest.mark.parametrize("name", list(EXPECTED))
    def test_exact_capability_sets(self, name):
        assert RBACRole.objects.get(name=name).capability_set() == set(EXPECTED[name])

    def test_superadmin_and_admin_hold_everything(self):
        for name in ("superadmin", "admin"):
            assert RBACRole.objects.get(name=name).capability_set() == set(caps.ALL_CAPABILITIES)

    def test_system_and_immutable_flags(self):
        sa = RBACRole.objects.get(name="superadmin")
        assert sa.is_system and sa.is_immutable
        assert RBACRole.objects.get(name="admin").is_immutable is False

    def test_engineer_excludes_admin_and_template_caps(self):
        eng = RBACRole.objects.get(name="engineer").capability_set()
        for c in (caps.CREDENTIAL_MANAGE, caps.INTEGRATION_MANAGE, caps.TLS_MANAGE,
                  caps.BACKUP_MANAGE, caps.MIB_MANAGE, caps.AGENT_MANAGE,
                  caps.COLLECTOR_MANAGE, caps.SSO_MANAGE, caps.CVE_MANAGE,
                  caps.USER_MANAGE, caps.RBAC_MANAGE, caps.CONFIG_TEMPLATE_EDIT,
                  caps.COMPLIANCE_TEMPLATE_EDIT, caps.CHATOPS_MANAGE):
            assert c not in eng
        for c in (caps.DEVICE_EDIT, caps.ALERT_MANAGE, caps.CIRCUIT_EDIT,
                  caps.CONFIG_PUSH, caps.COMPLIANCE_RUN, caps.CHATOPS_USE):
            assert c in eng

    def test_api_mirrors_engineer(self):
        assert (RBACRole.objects.get(name="api").capability_set()
                == RBACRole.objects.get(name="engineer").capability_set())


class TestLegacyMapping:
    def test_mapping_covers_every_legacy_role(self):
        assert set(caps.LEGACY_ROLE_TO_SYSTEM) == {r.value for r in Role}

    def test_mapping_targets_are_seeded(self):
        for target in caps.LEGACY_ROLE_TO_SYSTEM.values():
            assert RBACRole.objects.filter(name=target, is_system=True).exists()

    @pytest.mark.parametrize("legacy,role_name", list(caps.LEGACY_ROLE_TO_SYSTEM.items()))
    def test_user_resolves_to_expected_role(self, legacy, role_name):
        # Mirrors the data migration's per-user assignment (which runs over
        # existing users at deploy time) and confirms the FK + capabilities land.
        u = User.objects.create_user(username=f"u_{legacy}", password="x", role=legacy)
        u.rbac_role = RBACRole.objects.get(name=caps.LEGACY_ROLE_TO_SYSTEM[u.role])
        u.save(update_fields=["rbac_role"])
        assert u.rbac_role.name == role_name
        assert u.rbac_role.capability_set() == set(EXPECTED[role_name])


class TestRoleGuards:
    def test_unknown_capability_rejected(self):
        with pytest.raises(ValidationError):
            RBACRole.objects.create(name="bad", capabilities=[caps.DEVICE_VIEW, "not:a:cap"])

    def test_superadmin_cannot_be_deleted(self):
        with pytest.raises(ValidationError):
            RBACRole.objects.get(name="superadmin").delete()

    def test_superadmin_cannot_be_downscoped(self):
        sa = RBACRole.objects.get(name="superadmin")
        sa.capabilities = [caps.DEVICE_VIEW]
        with pytest.raises(ValidationError):
            sa.save()

    def test_admin_role_is_mutable_and_deletable(self):
        admin = RBACRole.objects.get(name="admin")
        admin.description = "changed"
        admin.save()
        assert RBACRole.objects.get(name="admin").description == "changed"
        admin.delete()  # no raise (admin is not immutable)
        assert not RBACRole.objects.filter(name="admin").exists()


class TestHasCapability:
    def test_superuser_bypass(self):
        su = User.objects.create_superuser(username="root", password="x", email="r@x.io")
        assert has_capability(su, caps.CREDENTIAL_MANAGE) is True
        assert has_capability(su, "not:a:real:cap") is True  # bypass ignores the catalog

    def test_role_capability_membership(self):
        u = User.objects.create_user(username="e", password="x", role="engineer",
                                     rbac_role=RBACRole.objects.get(name="engineer"))
        assert has_capability(u, caps.DEVICE_EDIT) is True
        assert has_capability(u, caps.CREDENTIAL_MANAGE) is False

    def test_no_role_has_no_capabilities(self):
        u = User.objects.create_user(username="n", password="x")
        # Phase B: User.save() auto-syncs rbac_role from the legacy role, so a
        # default user is a viewer. Clear the FK directly (bypassing save) to
        # exercise the genuinely-no-role path of has_capability.
        User.objects.filter(pk=u.pk).update(rbac_role=None)
        u.refresh_from_db()
        assert u.rbac_role is None
        assert has_capability(u, caps.DEVICE_VIEW) is False

    def test_unauthenticated_has_no_capabilities(self):
        assert has_capability(AnonymousUser(), caps.DEVICE_VIEW) is False

    def test_has_capability_permission_class(self, rf):
        Perm = HasCapability(caps.DEVICE_EDIT)
        u = User.objects.create_user(username="p", password="x", role="engineer",
                                     rbac_role=RBACRole.objects.get(name="engineer"))
        req = rf.get("/"); req.user = u
        assert Perm().has_permission(req, None) is True
        # Set rbac_role directly via the ORM (bypassing User.save()'s Phase-B
        # rbac_role↔role sync, which would otherwise re-derive it from role).
        User.objects.filter(pk=u.pk).update(rbac_role=RBACRole.objects.get(name="viewer"))
        u.refresh_from_db()
        req2 = rf.get("/"); req2.user = u
        assert Perm().has_permission(req2, None) is False
