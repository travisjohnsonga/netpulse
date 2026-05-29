"""
Abstract base class for vendor API integrations.

Each plugin manages one integration instance (one Meraki org, one Mist org,
one UniFi site, etc.).  Credentials are fetched from OpenBao via cred_path.
"""
import abc

from .models import VendorAlert, VendorDevice, VendorMetric


class VendorAPIPlugin(abc.ABC):
    """
    Base class for vendor API integrations.

    Subclasses must set ``vendor`` and implement ``fetch_devices`` and
    ``fetch_alerts``.  ``fetch_metrics`` and ``parse_webhook`` have
    sensible defaults.
    """

    vendor: str = ""  # override in subclass: "meraki", "mist", "unifi"

    def __init__(self, integration_id: str, config: dict, credentials: dict) -> None:
        self.integration_id = integration_id
        self.config = config           # from INTEGRATIONS_JSON entry
        self.credentials = credentials  # from OpenBao

    @abc.abstractmethod
    async def fetch_devices(self) -> list[VendorDevice]:
        """Poll vendor API for current device inventory and status."""
        ...

    @abc.abstractmethod
    async def fetch_alerts(self) -> list[VendorAlert]:
        """Poll vendor API for recent alerts/events."""
        ...

    async def fetch_metrics(self) -> list[VendorMetric]:
        """Optional: poll time-series metrics. Default returns empty list."""
        return []

    def parse_webhook(
        self, payload: dict, source_ip: str
    ) -> list[VendorAlert | VendorDevice]:
        """
        Parse an inbound vendor webhook payload.

        Default: return empty list (vendor does not support webhooks or not
        implemented in this plugin).
        """
        return []

    @property
    def poll_interval(self) -> int:
        """Polling interval in seconds, from integration config (default: 60)."""
        return int(self.config.get("poll_interval", 60))
