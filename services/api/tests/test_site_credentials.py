"""Tests for site credential assignments + resolution."""
import pytest

from apps.credentials.models import CredentialProfile, SiteCredential
from apps.credentials.site_resolve import (
    apply_site_credential, resolve_credential, resolve_credential_for_device,
)
from apps.devices.models import Device, DeviceRole, Site

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(db):
    site = Site.objects.create(name="WCO2")
    fw_role = DeviceRole.objects.create(name="Firewall", slug="firewall")
    sitewide = CredentialProfile.objects.create(name="wco2-ssh-snmp")
    fw_cred = CredentialProfile.objects.create(name="sonicwall-creds")
    return dict(site=site, fw_role=fw_role, sitewide=sitewide, fw_cred=fw_cred)


class TestResolve:
    def test_role_specific_wins_over_sitewide(self, setup):
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["sitewide"], role=None, priority=100)
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["fw_cred"], role=setup["fw_role"], priority=10)
        assert resolve_credential(setup["site"].id, setup["fw_role"].id).id == setup["fw_cred"].id

    def test_sitewide_fallback_when_no_role_match(self, setup):
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["sitewide"], role=None)
        other_role = DeviceRole.objects.create(name="Router", slug="router")
        assert resolve_credential(setup["site"].id, other_role.id).id == setup["sitewide"].id

    def test_none_when_no_rules(self, setup):
        assert resolve_credential(setup["site"].id, setup["fw_role"].id) is None

    def test_priority_within_sitewide(self, setup):
        low = CredentialProfile.objects.create(name="low")
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["sitewide"], role=None, priority=50)
        SiteCredential.objects.create(site=setup["site"], credential_profile=low, role=None, priority=5)
        assert resolve_credential(setup["site"].id, None).id == low.id


class TestResolveForDevice:
    def test_explicit_profile_always_wins(self, setup):
        explicit = CredentialProfile.objects.create(name="explicit")
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["sitewide"], role=None)
        d = Device.objects.create(hostname="d1", ip_address="10.0.0.1", site=setup["site"],
                                  role=setup["fw_role"], credential_profile=explicit)
        assert resolve_credential_for_device(d).id == explicit.id

    def test_apply_sets_when_unset(self, setup):
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["fw_cred"], role=setup["fw_role"])
        d = Device.objects.create(hostname="d2", ip_address="10.0.0.2", site=setup["site"], role=setup["fw_role"])
        applied = apply_site_credential(d)
        d.refresh_from_db()
        assert applied.id == setup["fw_cred"].id and d.credential_profile_id == setup["fw_cred"].id

    def test_apply_never_overrides(self, setup):
        explicit = CredentialProfile.objects.create(name="exp2")
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["sitewide"], role=None)
        d = Device.objects.create(hostname="d3", ip_address="10.0.0.3", site=setup["site"], credential_profile=explicit)
        assert apply_site_credential(d).id == explicit.id


class TestEndpoints:
    def test_crud_and_list(self, auth_client, setup):
        site = setup["site"]
        # create
        resp = auth_client.post(f"/api/sites/{site.id}/credentials/", {
            "credential_profile": setup["fw_cred"].id, "role": setup["fw_role"].id, "priority": 10,
        }, format="json")
        assert resp.status_code == 201
        cred_id = resp.json()["id"]
        # list
        lst = auth_client.get(f"/api/sites/{site.id}/credentials/").json()
        assert len(lst) == 1 and lst[0]["role_name"] == "Firewall" and lst[0]["credential_profile_name"] == "sonicwall-creds"
        # delete
        assert auth_client.delete(f"/api/sites/{site.id}/credentials/{cred_id}/").status_code == 204
        assert auth_client.get(f"/api/sites/{site.id}/credentials/").json() == []

    def test_suggest_credential(self, auth_client, setup):
        SiteCredential.objects.create(site=setup["site"], credential_profile=setup["sitewide"], role=None)
        resp = auth_client.get(f"/api/sites/{setup['site'].id}/suggest-credential/")
        assert resp.status_code == 200
        b = resp.json()
        assert b["name"] == "wco2-ssh-snmp" and b["scope"] == "all roles"

    def test_suggest_none(self, auth_client, setup):
        resp = auth_client.get(f"/api/sites/{setup['site'].id}/suggest-credential/")
        assert resp.json()["credential_profile"] is None
