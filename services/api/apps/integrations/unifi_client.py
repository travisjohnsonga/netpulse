"""
Minimal UniFi controller REST client (self-hosted / UDM "classic" API).

Used to test a controller's connection and pull its managed devices/clients for
import. Self-signed controller certs are the norm, so TLS verification defaults
off; ``trust_env=False`` stops requests' env-merge from silently overriding the
per-request ``verify`` with REQUESTS_CA_BUNDLE (see CLAUDE.md).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class UnifiError(Exception):
    """A UniFi controller request failed (login/HTTP/parse)."""


class UnifiClient:
    def __init__(self, host: str, port: int, username: str, password: str,
                 site_id: str = "default", verify_ssl: bool = False, timeout: int = 15):
        import requests

        self.base_url = f"https://{host}:{port}"
        self.site_id = site_id or "default"
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.session = requests.Session()
        # Don't let REQUESTS_CA_BUNDLE/HTTP(S)_PROXY env override our verify= choice.
        self.session.trust_env = False
        self.session.verify = verify_ssl
        self._logged_in = False
        # UniFi OS (UDM/Cloud Key Gen2+/newer firmware) vs. classic self-hosted
        # controller. Set during login(); changes the auth + data API paths.
        self._unifi_os = False

    def login(self) -> None:
        """Authenticate, trying UniFi OS first then the classic controller.

        UniFi OS (UDM, Cloud Key Gen2+, recent firmware) authenticates at
        ``/api/auth/login`` and proxies the Network app under ``/proxy/network``;
        the classic self-hosted controller uses ``/api/login`` directly. We try
        the OS endpoint first and fall back to classic, recording which one
        worked so subsequent requests hit the right path.
        """
        errors = []
        for endpoint in ("/api/auth/login", "/api/login"):
            try:
                resp = self.session.post(
                    f"{self.base_url}{endpoint}",
                    json={"username": self.username, "password": self.password},
                    timeout=self.timeout, verify=self.verify_ssl,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{endpoint}: {exc}")
                continue
            if resp.status_code == 200:
                self._logged_in = True
                self._unifi_os = endpoint == "/api/auth/login"
                return
            errors.append(f"{endpoint}: HTTP {resp.status_code}")
        raise UnifiError(
            f"UniFi login failed for {self.base_url} — tried both auth endpoints "
            f"(/api/auth/login, /api/login): {'; '.join(errors)}"
        )

    def _api(self, path: str) -> str:
        """Prefix data paths with /proxy/network on UniFi OS controllers."""
        return f"/proxy/network{path}" if self._unifi_os else path

    def logout(self) -> None:
        path = "/api/auth/logout" if self._unifi_os else "/api/logout"
        try:
            self.session.post(f"{self.base_url}{path}", timeout=5, verify=self.verify_ssl)
        except Exception:  # noqa: BLE001 — best-effort
            pass

    def _get(self, path: str, timeout: int | None = None) -> list:
        try:
            resp = self.session.get(f"{self.base_url}{self._api(path)}", timeout=timeout or self.timeout,
                                    verify=self.verify_ssl)
            resp.raise_for_status()
            return resp.json().get("data", []) or []
        except Exception as exc:  # noqa: BLE001
            raise UnifiError(f"UniFi request {path} failed: {exc}") from exc

    def get_devices(self, device_type: str | None = None) -> list:
        """UniFi-managed devices (APs/switches/gateways) for the site.

        ``device_type`` filters by the UniFi ``type`` field ('uap', 'usw',
        'ugw', 'udm'); None returns every device. The ``stat/device`` payload
        carries the rich per-device stats (radio_table_stats, cpu/mem, uplink,
        satisfaction) used by both inventory sync and telemetry collection.
        """
        devices = self._get(f"/api/s/{self.site_id}/stat/device", timeout=30)
        if device_type:
            devices = [d for d in devices if (d.get("type") or "").lower() == device_type]
        return devices

    def get_ap_stats(self) -> list:
        """All access points (type 'uap') with their radio/health stats."""
        return self.get_devices(device_type="uap")

    def get_gateway_stats(self) -> dict | None:
        """The UDM/gateway/console device dict (type ugw/udm/usg/uxg), or None."""
        for d in self.get_devices():
            if (d.get("type") or "").lower() in ("ugw", "udm", "usg", "uxg", "ucg"):
                return d
        return None

    def get_system_health(self) -> list:
        """Site health subsystems (wan/wan2/vpn/www/lan/wlan) with status/throughput."""
        return self._get(f"/api/s/{self.site_id}/stat/health")

    def get_clients(self) -> list:
        """Connected client stations for the site."""
        return self._get(f"/api/s/{self.site_id}/stat/sta", timeout=30)

    def get_sites(self) -> list:
        """Sites available on the controller (each item has a 'name')."""
        return self._get("/api/self/sites")

    def __enter__(self) -> "UnifiClient":
        self.login()
        return self

    def __exit__(self, *args) -> None:
        self.logout()
