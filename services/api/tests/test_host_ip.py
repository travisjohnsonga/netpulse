"""Tests for host-IP detection + local-collector IP self-heal."""
import pytest
from django.test import override_settings

from apps.collectors import host_ip
from apps.collectors.host_ip import get_host_ip, is_docker_ip
from apps.collectors.management.commands.register_local_collector import register_local_collector
from apps.collectors.models import Collector

pytestmark = pytest.mark.django_db


class TestIsDockerIp:
    def test_detects_bridge_range(self):
        assert is_docker_ip("172.18.0.5") and is_docker_ip("172.17.0.1")
        assert not is_docker_ip("192.168.1.10")
        assert not is_docker_ip("10.0.0.5")
        assert not is_docker_ip("not-an-ip") and not is_docker_ip(None)


class TestGetHostIp:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("NETPULSE_HOST_IP", "192.168.50.10")
        with override_settings(COLLECTOR_IP="10.0.0.1", ALLOWED_HOSTS=["10.0.0.2"]):
            assert get_host_ip() == "192.168.50.10"

    def test_invalid_env_override_ignored_uses_collector_ip(self, monkeypatch):
        monkeypatch.setenv("NETPULSE_HOST_IP", "# not an ip")
        with override_settings(COLLECTOR_IP="10.0.0.7", ALLOWED_HOSTS=["*"]):
            assert get_host_ip() == "10.0.0.7"

    def test_falls_back_to_allowed_hosts(self, monkeypatch):
        monkeypatch.delenv("NETPULSE_HOST_IP", raising=False)
        with override_settings(COLLECTOR_IP="", ALLOWED_HOSTS=["localhost", "127.0.0.1", "192.168.9.9"]):
            assert get_host_ip() == "192.168.9.9"

    def test_returns_none_when_nothing_and_no_route(self, monkeypatch):
        monkeypatch.delenv("NETPULSE_HOST_IP", raising=False)
        # Force the source-route fallback to fail.
        def boom(*a, **k):
            raise OSError("no route")
        monkeypatch.setattr(host_ip.socket, "socket", boom)
        with override_settings(COLLECTOR_IP="", ALLOWED_HOSTS=["localhost", "127.0.0.1"]):
            assert get_host_ip() is None


class TestCollectorSelfHeal:
    def test_corrects_docker_ip(self, monkeypatch):
        monkeypatch.setenv("NETPULSE_HOST_IP", "192.168.40.5")
        # Pre-existing local collector with a stale Docker bridge IP.
        Collector.objects.create(
            name="NetPulse Local", collector_type=Collector.CollectorType.LOCAL,
            collector_ip="172.18.0.22", api_key_hash="local-server-no-api-key",
            status=Collector.Status.ACTIVE,
        )
        c = register_local_collector()
        assert c.collector_ip == "192.168.40.5"

    def test_register_uses_host_ip(self, monkeypatch):
        monkeypatch.setenv("NETPULSE_HOST_IP", "192.168.40.5")
        c = register_local_collector()
        assert c.collector_ip == "192.168.40.5" and not is_docker_ip(c.collector_ip)
