import pytest
from apps.compliance.models import CompliancePolicy, CompliancePolicyRule, ComplianceResult
from apps.devices.models import Device

pytestmark = pytest.mark.django_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def device():
    return Device.objects.create(hostname="sw-01", ip_address="10.0.0.1")


@pytest.fixture
def policy():
    return CompliancePolicy.objects.create(name="CIS Baseline", description="CIS hardening checks")


@pytest.fixture
def rule(policy):
    return CompliancePolicyRule.objects.create(
        policy=policy,
        name="SSH Banner Check",
        check_type="contains",
        check_expression="Authorized use only",
    )


@pytest.fixture
def result(device, policy, rule):
    return ComplianceResult.objects.create(
        device=device, policy=policy, rule=rule,
        outcome="pass",
    )


# ── CompliancePolicy ──────────────────────────────────────────────────────────

class TestCompliancePolicyEndpoints:
    def test_list_policies(self, auth_client, policy):
        resp = auth_client.get("/api/compliance/policies/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_policy(self, auth_client):
        resp = auth_client.post("/api/compliance/policies/", {
            "name": "PCI DSS Controls",
            "description": "Payment card security",
        })
        assert resp.status_code == 201
        assert resp.json()["name"] == "PCI DSS Controls"

    def test_create_policy_default_active(self, auth_client):
        resp = auth_client.post("/api/compliance/policies/", {"name": "Test Policy"}, format="json")
        assert resp.json()["is_active"] is True

    def test_retrieve_policy_includes_rules(self, auth_client, policy, rule):
        resp = auth_client.get(f"/api/compliance/policies/{policy.pk}/")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rules"]) == 1
        assert data["rules"][0]["name"] == "SSH Banner Check"

    def test_update_policy(self, auth_client, policy):
        resp = auth_client.patch(f"/api/compliance/policies/{policy.pk}/", {"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_delete_policy(self, auth_client, policy):
        resp = auth_client.delete(f"/api/compliance/policies/{policy.pk}/")
        assert resp.status_code == 204
        assert not CompliancePolicy.objects.filter(pk=policy.pk).exists()

    def test_filter_by_is_active(self, auth_client, policy):
        CompliancePolicy.objects.create(name="Inactive", is_active=False)
        resp = auth_client.get("/api/compliance/policies/?is_active=true")
        assert resp.status_code == 200
        assert all(p["is_active"] is True for p in resp.json()["results"])

    def test_search_by_name(self, auth_client, policy):
        CompliancePolicy.objects.create(name="HIPAA Controls")
        resp = auth_client.get("/api/compliance/policies/?search=CIS")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()["results"]]
        assert "CIS Baseline" in names
        assert "HIPAA Controls" not in names

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/compliance/policies/")
        assert resp.status_code == 401


# ── CompliancePolicyRule ──────────────────────────────────────────────────────

class TestCompliancePolicyRuleEndpoints:
    def test_list_rules(self, auth_client, rule):
        resp = auth_client.get("/api/compliance/rules/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_create_rule(self, auth_client, policy):
        resp = auth_client.post("/api/compliance/rules/", {
            "policy": policy.pk,
            "name": "NTP Configured",
            "check_type": "regex",
            "check_expression": r"ntp server \d+\.\d+\.\d+\.\d+",
        })
        assert resp.status_code == 201
        assert resp.json()["check_type"] == "regex"

    def test_retrieve_rule(self, auth_client, rule):
        resp = auth_client.get(f"/api/compliance/rules/{rule.pk}/")
        assert resp.status_code == 200
        assert resp.json()["name"] == "SSH Banner Check"

    def test_update_rule(self, auth_client, rule):
        resp = auth_client.patch(f"/api/compliance/rules/{rule.pk}/", {"is_active": False})
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    def test_delete_rule(self, auth_client, rule):
        resp = auth_client.delete(f"/api/compliance/rules/{rule.pk}/")
        assert resp.status_code == 204

    def test_filter_by_policy(self, auth_client, rule, policy):
        other_policy = CompliancePolicy.objects.create(name="Other Policy")
        CompliancePolicyRule.objects.create(
            policy=other_policy, name="Other Rule",
            check_type="contains", check_expression="test",
        )
        resp = auth_client.get(f"/api/compliance/rules/?policy={policy.pk}")
        assert resp.status_code == 200
        assert all(r["policy"] == policy.pk for r in resp.json()["results"])

    def test_filter_by_check_type(self, auth_client, rule, policy):
        CompliancePolicyRule.objects.create(
            policy=policy, name="JMESPath Rule",
            check_type="jmespath", check_expression="interfaces.*.state",
        )
        resp = auth_client.get("/api/compliance/rules/?check_type=contains")
        assert resp.status_code == 200
        assert all(r["check_type"] == "contains" for r in resp.json()["results"])

    def test_rule_deleted_when_policy_deleted(self, policy, rule):
        policy.delete()
        assert not CompliancePolicyRule.objects.filter(pk=rule.pk).exists()


# ── ComplianceResult ──────────────────────────────────────────────────────────

class TestComplianceResultEndpoints:
    def test_list_results(self, auth_client, result):
        resp = auth_client.get("/api/compliance/results/")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_retrieve_result(self, auth_client, result):
        resp = auth_client.get(f"/api/compliance/results/{result.pk}/")
        assert resp.status_code == 200
        assert resp.json()["outcome"] == "pass"

    def test_results_are_read_only_no_create(self, auth_client, device, policy, rule):
        resp = auth_client.post("/api/compliance/results/", {
            "device": device.pk, "policy": policy.pk,
            "rule": rule.pk, "outcome": "pass",
        })
        assert resp.status_code == 405

    def test_results_are_read_only_no_delete(self, auth_client, result):
        resp = auth_client.delete(f"/api/compliance/results/{result.pk}/")
        assert resp.status_code == 405

    def test_filter_by_device(self, auth_client, result, device, policy, rule):
        other_device = Device.objects.create(hostname="other-sw", ip_address="10.0.0.2")
        ComplianceResult.objects.create(device=other_device, policy=policy, rule=rule, outcome="fail")
        resp = auth_client.get(f"/api/compliance/results/?device={device.pk}")
        assert resp.status_code == 200
        assert all(r["device"] == device.pk for r in resp.json()["results"])

    def test_filter_by_outcome(self, auth_client, result, device, policy, rule):
        ComplianceResult.objects.create(device=device, policy=policy, rule=rule, outcome="fail")
        resp = auth_client.get("/api/compliance/results/?outcome=pass")
        assert resp.status_code == 200
        assert all(r["outcome"] == "pass" for r in resp.json()["results"])

    def test_unauthenticated_rejected(self, api_client):
        resp = api_client.get("/api/compliance/results/")
        assert resp.status_code == 401


# ── Model Tests ───────────────────────────────────────────────────────────────

class TestComplianceModels:
    def test_policy_str(self, policy):
        assert str(policy) == "CIS Baseline"

    def test_rule_str(self, rule):
        assert "CIS Baseline" in str(rule)
        assert "SSH Banner Check" in str(rule)

    def test_outcome_choices(self):
        for val, _ in ComplianceResult.Outcome.choices:
            assert val in ("pass", "fail", "error")

    def test_check_type_choices(self):
        for val, _ in CompliancePolicyRule.CheckType.choices:
            assert val in ("regex", "contains", "jmespath", "napalm")
