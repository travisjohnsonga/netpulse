"""Tests for the public agent install-script endpoints (/agent/install[.ps1])."""
import os

import pytest

pytestmark = pytest.mark.django_db


def _seed(agent_dir, name, body):
    scripts = os.path.join(agent_dir, "scripts")
    os.makedirs(scripts, exist_ok=True)
    with open(os.path.join(scripts, name), "w") as fh:
        fh.write(body)


class TestWindowsInstallScript:
    def test_serves_ps1_as_plaintext(self, api_client, settings, tmp_path):
        settings.AGENT_DIR = str(tmp_path)
        _seed(str(tmp_path), "install.ps1", "param([string]$Server,[string]$Token)\nWrite-Host hi\n")
        resp = api_client.get("/agent/install.ps1")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/plain")
        assert b"$Server" in resp.content and b"$Token" in resp.content

    def test_404_when_missing(self, api_client, settings, tmp_path):
        # AGENT_DIR with no scripts/install.ps1 → 404, not a 500.
        settings.AGENT_DIR = str(tmp_path)
        assert api_client.get("/agent/install.ps1").status_code == 404

    def test_real_script_is_served(self, api_client):
        # With the default AGENT_DIR (the repo agent/ dir) the real install.ps1
        # is reachable — guards against the route regressing to a 404.
        resp = api_client.get("/agent/install.ps1")
        assert resp.status_code == 200
        assert b"-Server" in resp.content or b"$Server" in resp.content
