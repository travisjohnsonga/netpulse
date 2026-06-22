"""RBAC for ComplianceTemplate authoring.

Authoring a compliance template means writing a server-side-rendered Jinja2
string (an SSTI surface even behind the sandboxed environment), so create/
update/delete are admin-only. Read/list/preview stay open to the operational
roles that need to view templates.
"""
import pytest

from apps.compliance.models import ComplianceTemplate

TEMPLATES_URL = "/api/compliance/templates/"


def _template_payload(name="rbac-tmpl"):
    return {
        "name": name,
        "description": "rbac test",
        "platform": "ios_xe",
        "template_content": "hostname {{ device.hostname }}\n",
        "variables": {},
        "enabled": True,
    }


@pytest.fixture
def existing_template(db):
    return ComplianceTemplate.objects.create(
        name="seed-tmpl",
        platform="ios_xe",
        template_content="hostname {{ device.hostname }}\n",
    )


@pytest.mark.django_db
class TestComplianceTemplateRBAC:
    def test_engineer_cannot_create(self, engineer_client):
        resp = engineer_client.post(TEMPLATES_URL, _template_payload(), format="json")
        assert resp.status_code == 403
        assert not ComplianceTemplate.objects.filter(name="rbac-tmpl").exists()

    def test_engineer_cannot_update(self, engineer_client, existing_template):
        resp = engineer_client.patch(
            f"{TEMPLATES_URL}{existing_template.id}/",
            {"template_content": "{{ 7 * 7 }}"},
            format="json",
        )
        assert resp.status_code == 403
        existing_template.refresh_from_db()
        assert "{{ 7 * 7 }}" not in existing_template.template_content

    def test_engineer_cannot_delete(self, engineer_client, existing_template):
        resp = engineer_client.delete(f"{TEMPLATES_URL}{existing_template.id}/")
        assert resp.status_code == 403
        assert ComplianceTemplate.objects.filter(id=existing_template.id).exists()

    def test_admin_can_create(self, admin_client):
        resp = admin_client.post(TEMPLATES_URL, _template_payload(), format="json")
        assert resp.status_code == 201, resp.content
        assert ComplianceTemplate.objects.filter(name="rbac-tmpl").exists()

    def test_admin_can_update(self, admin_client, existing_template):
        resp = admin_client.patch(
            f"{TEMPLATES_URL}{existing_template.id}/",
            {"description": "updated"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        existing_template.refresh_from_db()
        assert existing_template.description == "updated"

    def test_engineer_can_list(self, engineer_client, existing_template):
        resp = engineer_client.get(TEMPLATES_URL)
        assert resp.status_code == 200

    def test_engineer_can_retrieve(self, engineer_client, existing_template):
        resp = engineer_client.get(f"{TEMPLATES_URL}{existing_template.id}/")
        assert resp.status_code == 200
        assert resp.data["id"] == existing_template.id
