"""Tests for the example seeders: seed_sites + seed_hostname_rules."""
import pytest
from django.core.management import call_command

from apps.devices.models import HostnameRule, Site

pytestmark = pytest.mark.django_db


class TestSeedSites:
    def test_creates_placeholder_sites_when_empty(self):
        assert not Site.objects.exists()
        call_command("seed_sites")
        names = set(Site.objects.values_list("name", flat=True))
        assert names == {"Site 1", "Site 2"}
        # Slugs are auto-generated and unique.
        assert set(Site.objects.values_list("slug", flat=True)) == {"site-1", "site-2"}

    def test_skips_when_sites_exist(self):
        Site.objects.create(name="HQ")
        call_command("seed_sites")
        # Only the pre-existing site remains — no placeholders added.
        assert list(Site.objects.values_list("name", flat=True)) == ["HQ"]

    def test_idempotent(self):
        call_command("seed_sites")
        call_command("seed_sites")  # sites now exist → no-op
        assert Site.objects.filter(name__startswith="Site ").count() == 2


class TestSeedHostnameRules:
    def test_seeds_generic_rules_disabled(self):
        call_command("seed_device_roles")
        call_command("seed_sites")
        call_command("seed_hostname_rules")

        rules = {r.name: r for r in HostnameRule.objects.all()}
        # Generic site + role examples are present.
        assert "Site 1 devices" in rules and "Site 2 devices" in rules
        assert "Firewalls (fw/fwl/pfw)" in rules
        # All examples ship disabled for admin review.
        assert all(not r.enabled for r in rules.values())

    def test_site_rules_link_to_seeded_sites(self):
        call_command("seed_sites")
        call_command("seed_hostname_rules")
        site1_rule = HostnameRule.objects.get(name="Site 1 devices")
        assert site1_rule.rule_type == "site"
        assert site1_rule.site is not None and site1_rule.site.name == "Site 1"

    def test_no_lab_specific_values(self):
        call_command("seed_sites")
        call_command("seed_hostname_rules")
        blob = " ".join(
            f"{r.name} {r.pattern}" for r in HostnameRule.objects.all()
        ).lower()
        assert "wco2" not in blob and "waco" not in blob

    def test_idempotent(self):
        call_command("seed_sites")
        call_command("seed_hostname_rules")
        first = HostnameRule.objects.count()
        call_command("seed_hostname_rules")
        assert HostnameRule.objects.count() == first
