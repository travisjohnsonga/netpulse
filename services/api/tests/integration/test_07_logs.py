"""Integration: logs — auth required; tolerate OpenSearch being unavailable."""
import pytest

pytestmark = pytest.mark.django_db


class TestLogsEndpoint:
    def test_requires_auth(self, api_client):
        assert api_client.get("/api/logs/").status_code == 401

    def test_returns_documented_shape(self, auth_client):
        # LogQueryView always returns 200 with a {count, results, summary}
        # envelope: empty when OpenSearch is unavailable (it catches the error),
        # populated when the api container can reach a live OpenSearch. We
        # assert the contract, not a specific count, so the suite is stable in
        # both environments.
        resp = auth_client.get("/api/logs/")
        assert resp.status_code == 200
        body = resp.json()
        assert "count" in body and isinstance(body["count"], int)
        assert "results" in body and isinstance(body["results"], list)
        assert "summary" in body
        assert "by_severity" in body["summary"]
        # results length is bounded by the page size and never exceeds count.
        assert len(body["results"]) <= body["count"] or body["count"] == 0
